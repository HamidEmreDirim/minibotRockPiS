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

# --- CONFIGURATION ---
DEFAULT_IP = "192.168.50.22"
DEFAULT_PORT = 5000
DEFAULT_DEVICE_ALIAS = "/dev/thermal_camera"
TARGET_FPS = 20  
FIXED_HEIGHT = 192 
WATCHDOG_TIMEOUT = 5

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("thermal_stream")

running = True

class WatchdogError(Exception):
    pass

def watchdog_handler(signum, frame):
    raise WatchdogError("Watchdog Timer Expired!")

def handle_signal(signum, frame):
    global running
    logger.info(f"Received signal {signum}. Stopping...")
    running = False

def find_working_camera(preferred_alias):
    candidates = [preferred_alias, "/dev/video0", "/dev/video1"] + glob.glob("/dev/video*")
    candidates = list(dict.fromkeys(candidates))

    logger.info(f"Scanning for cameras: {candidates}")

    for dev_path in candidates:
        if not os.path.exists(dev_path): continue

        try:
            logger.info(f"Testing device: {dev_path}...")
            # Watchdog just for opening
            signal.alarm(WATCHDOG_TIMEOUT) 
            cap = cv2.VideoCapture(dev_path, cv2.CAP_V4L2)
            signal.alarm(0)

            if cap.isOpened():
                signal.alarm(WATCHDOG_TIMEOUT)
                ret, _ = cap.read()
                signal.alarm(0)

                if ret:
                    logger.info(f"SUCCESS: Found working camera at {dev_path}")
                    return cap
                else:
                    cap.release()
        except Exception:
            signal.alarm(0) # Ensure alarm is off on error
            pass
    return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default=DEFAULT_IP)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device", default=DEFAULT_DEVICE_ALIAS)
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGALRM, watchdog_handler)

    # ---------------------------------------------------------
    # STEP 1: INITIALIZE CAMERA (CRITICAL)
    # If this fails, we EXIT to trigger Systemd Driver Reset
    # ---------------------------------------------------------
    cap = None
    for i in range(3):
        cap = find_working_camera(args.device)
        if cap: break
        time.sleep(3)

    if not cap:
        logger.error("CRITICAL: No working camera found. Exiting to reset drivers.")
        sys.exit(1)

    # ---------------------------------------------------------
    # STEP 2: MAIN LOOP (RESILIENT)
    # We stay in this loop forever. Network errors do NOT kill us.
    # ---------------------------------------------------------
    sock = None
    frame_interval = 1.0 / TARGET_FPS
    last_connect_retry = 0
    CONNECT_RETRY_INTERVAL = 2.0 # Wait 2s between connection attempts

    logger.info("Camera ready. Entering stream loop...")

    try:
        while running:
            start_time = time.time()

            # --- A. READ CAMERA (Watchdog Protected) ---
            # We MUST read every frame to keep the buffer clean
            try:
                signal.alarm(WATCHDOG_TIMEOUT)
                ret, frame = cap.read()
                signal.alarm(0) # Clear immediately
            except WatchdogError:
                logger.error("Camera froze (Watchdog)! Exiting to reset drivers.")
                sys.exit(1)

            if not ret:
                logger.error("Camera returned empty frame. Exiting.")
                sys.exit(1)

            # --- B. MANAGE NETWORK (Non-Critical) ---
            # If unconnected, try to connect periodically
            if sock is None:
                if (time.time() - last_connect_retry) > CONNECT_RETRY_INTERVAL:
                    try:
                        logger.info(f"Attempting connection to {args.ip}...")
                        # Set a strict timeout for the socket operation so it doesn't hang
                        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        temp_sock.settimeout(1.0) 
                        temp_sock.connect((args.ip, args.port))
                        temp_sock.settimeout(None) # Back to blocking mode
                        temp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        sock = temp_sock
                        logger.info("Connected!")
                    except (OSError, socket.timeout):
                        # Quietly fail and retry later
                        last_connect_retry = time.time()
            
            # --- C. SEND DATA (If Connected) ---
            if sock:
                try:
                    gray_half = frame[0:FIXED_HEIGHT, :]
                    if gray_half.dtype != np.uint8:
                        gray_half = cv2.normalize(gray_half, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
                    inferno_frame = cv2.applyColorMap(gray_half, cv2.COLORMAP_INFERNO)
                    
                    h, w, c = inferno_frame.shape
                    header = struct.pack("!IIII", h, w, c, 0)
                    
                    sock.sendall(header)
                    sock.sendall(inferno_frame.tobytes())
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    logger.warning(f"Connection lost ({e}). resetting socket...")
                    sock.close()
                    sock = None
                    last_connect_retry = time.time() # Delay slightly before reconnecting

            # --- D. FPS SLEEP ---
            elapsed = time.time() - start_time
            wait_time = frame_interval - elapsed
            if wait_time > 0:
                time.sleep(wait_time)

    except Exception as e:
        logger.error(f"Unexpected main loop error: {e}")
        sys.exit(1)
    finally:
        if cap: cap.release()
        try: sock.close()
        except: pass

if __name__ == "__main__":
    main()
