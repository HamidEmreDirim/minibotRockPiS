#!/usr/bin/env python3
import cv2
import socket
import struct
import sys
import time
import numpy as np
import os
import logging
import signal
import argparse
import glob


# --- DEFAULT CONFIGURATION ---
# (Can be overridden via command line args)
DEFAULT_IP = "192.168.50.22"
DEFAULT_PORT = 5000
DEFAULT_DEVICE = "/dev/thermal_camera"
TARGET_FPS = 20  # Limit FPS to save bandwidth/CPU
RETRY_DELAY = 2.0

# Setup Logging for Systemd
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("thermal_stream")

# Global flag for graceful shutdown
running = True

def handle_signal(signum, frame):
    """Handle system signals (like SIGTERM from systemd) to stop gracefully."""
    global running
    logger.info(f"Received signal {signum}. Stopping service...")
    running = False

def connect_socket(ip, port):
    """Establishes a socket connection to the receiver."""
    while running:
        try:
            logger.info(f"Connecting to {ip}:{port}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(5.0)
            sock.connect((ip, port))
            sock.settimeout(None) # Remove timeout for blocking sends
            logger.info("Connected to receiver.")
            return sock
        except (OSError, socket.timeout) as e:
            logger.warning(f"Connection failed: {e}. Retrying in {RETRY_DELAY}s...")
            sock.close()
            time.sleep(RETRY_DELAY)
    return None


def get_camera(device_path):
    """Attempts to open the device, with auto-discovery fallback."""
    
    # List of candidates: The config path + any /dev/video* entries
    candidates = [device_path] + sorted(glob.glob("/dev/video*"))
    
    # Remove duplicates while preserving order
    candidates = list(dict.fromkeys(candidates))

    for current_dev in candidates:
        if not os.path.exists(current_dev):
            continue

        try:
            # We use a context manager logic for safety
            cap = cv2.VideoCapture(current_dev, cv2.CAP_V4L2)
            
            # Force MJPG or RAW if supported (Thermal Master likes YUYV or MJPG)
            # cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
            
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    logger.info(f"Success! Camera opened at {current_dev}")
                    return cap
                else:
                    logger.warning(f"Device {current_dev} opened but returned no frame.")
                    cap.release()
            else:
                pass # Just failed to open
        except Exception as e:
            logger.warning(f"Error checking {current_dev}: {e}")

    logger.error("Could not find any working thermal camera.")
    time.sleep(RETRY_DELAY)
    return None


def main():
    # Parse arguments for flexibility
    parser = argparse.ArgumentParser(description="Thermal Camera Streamer")
    parser.add_argument("--ip", default=DEFAULT_IP, help="Destination IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Destination Port")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="Path to video device")
    args = parser.parse_args()

    # Register Signal Handlers (SIGINT=Ctrl+C, SIGTERM=Systemd Stop)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Frame timing
    frame_interval = 1.0 / TARGET_FPS

    while running:
        sock = connect_socket(args.ip, args.port)
        if not sock: break # Shutdown requested during connection

        cap = get_camera(args.device)
        if not cap: 
            sock.close()
            break # Shutdown requested during camera init

        logger.info("Stream loop starting.")
        
        try:
            while running:
                start_time = time.time()

                ret, frame = cap.read()
                if not ret:
                    logger.error("Lost frame from camera. Reinitializing...")
                    break 

                height, width = frame.shape[:2]

                # --- SLICING LOGIC ---
                # Detect "Tall" stacking (common in thermal raw feeds)
                if height > width:
                    target_h = 192
                    if height >= (target_h * 2):
                        gray_half = frame[height-target_h:, :]
                    else:
                        gray_half = frame[height//2:, :]
                else:
                    gray_half = frame[:, :width//2]

                # --- NORMALIZE & COLOR ---
                # Normalize 16-bit raw data to 8-bit for visualization
                if gray_half.dtype == np.uint16:
                    gray_half = cv2.normalize(gray_half, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
                elif gray_half.dtype != np.uint8:
                     # Safety fallback for float or other types
                    gray_half = cv2.normalize(gray_half, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)

                inferno_frame = cv2.applyColorMap(gray_half, cv2.COLORMAP_INFERNO)
                
                # --- PACKET CREATION ---
                h_out, w_out, c_out = inferno_frame.shape
                # Header: ! = Network Endian, I = unsigned int (4 bytes)
                header = struct.pack("!IIII", h_out, w_out, c_out, 0)
                
                # Send
                sock.sendall(header)
                sock.sendall(inferno_frame.tobytes())

                # --- FPS CONTROL ---
                elapsed = time.time() - start_time
                wait_time = frame_interval - elapsed
                if wait_time > 0:
                    time.sleep(wait_time)

        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.error(f"Network error: {e}. Reconnecting...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            # Clean up resources before restarting loop or exiting
            if cap: cap.release()
            try: sock.close()
            except: pass
            time.sleep(1)

    logger.info("Service shutdown complete.")

if __name__ == "__main__":
    main()
