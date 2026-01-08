#!/usr/bin/env python3
import cv2
import socket
import struct
import sys
import time
import glob
import numpy as np

# --- CONFIGURATION ---
DEST_IP = "192.168.50.22"   
DEST_PORT = 5000
RETRY_SLEEP = 2.0 

def find_camera_device():
    """Find a working /dev/video* and return the path."""
    candidates = sorted(glob.glob("/dev/video*"))
    if not candidates:
        print("[thermal_stream] No /dev/video* found.")
        return None

    for dev in candidates:
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            continue

        for _ in range(5):
            ret, frame = cap.read()
            if ret and frame is not None:
                print(f"[thermal_stream] Found valid camera at {dev}")
                return cap, dev
            time.sleep(0.1)

        cap.release()
    return None

def wait_for_camera():
    while True:
        result = find_camera_device()
        if result is not None:
            return result
        print(f"[thermal_stream] Camera not ready, retrying in {RETRY_SLEEP}s.")
        time.sleep(RETRY_SLEEP)

def connect_socket():
    while True:
        try:
            print(f"[thermal_stream] Connecting to {DEST_IP}:{DEST_PORT} ...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(5.0) 
            sock.connect((DEST_IP, DEST_PORT))
            sock.settimeout(None)
            print("[thermal_stream] Connected to receiver.")
            return sock
        except (OSError, socket.timeout) as e:
            print(f"[thermal_stream] Connect failed: {e}, retrying in {RETRY_SLEEP}s.")
            time.sleep(RETRY_SLEEP)

def main():
    cap, dev = wait_for_camera()
    frame_count = 0

    while True:
        sock = connect_socket()

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    print("[thermal_stream] Frame Error. Resetting camera...")
                    cap.release()
                    cap, dev = wait_for_camera()
                    break
                
                # Debug Shape every 100 frames
                if frame_count % 100 == 0:
                    print(f"[thermal_stream] Raw Shape: {frame.shape}")
                frame_count += 1

                # --- NEW CROP LOGIC (Top-Bottom) ---
                height, width, channels = frame.shape
                
                # Check if image is "Tall" (Stacked Vertically)
                if height > width:
                    # Take the BOTTOM half (based on your screenshot)
                    # We assume standard 192 height. 
                    # If total is 386, we take the last 192 pixels to avoid the padding at the top.
                    target_h = 192
                    if height >= (target_h * 2):
                        # Safest bet: take exactly 192 lines from the bottom
                        gray_half = frame[height-target_h:, :]
                    else:
                        # Fallback: Just split in half
                        gray_half = frame[height//2:, :]
                else:
                    # Original logic (Side-by-Side)
                    gray_half = frame[:, :width//2]

                # --- COLOR & SEND ---
                inferno_frame = cv2.applyColorMap(gray_half, cv2.COLORMAP_INFERNO)

                h_out, w_out, c_out = inferno_frame.shape
                
                header = struct.pack("!IIII", h_out, w_out, c_out, 0)
                payload = inferno_frame.tobytes()

                sock.sendall(header)
                sock.sendall(payload)

        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print(f"[thermal_stream] Socket error: {e}. Reconnecting...")
        finally:
            try: sock.close()
            except: pass
            time.sleep(1)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
