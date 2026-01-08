#!/usr/bin/env python3
import cv2
import time

# UPDATE THIS INDEX based on v4l2-ctl output! 
# On RPi5, USB cams often start at index 0, 4, or 8.
DEVICE_INDEX = 0 

def main():
    # RPi5 handles V4L2 well.
    cap = cv2.VideoCapture(DEVICE_INDEX, cv2.CAP_V4L2)

    if not cap.isOpened():
        print(f"ERROR: Could not open video device {DEVICE_INDEX}")
        print("Tip: Try changing DEVICE_INDEX to 4 or 8 if 0 fails.")
        return

    # Set resolution (Adjust based on your specific Thermal Camera specs)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

    print("Starting capture loop. Press Ctrl+C to stop.\n")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to grab frame")
                time.sleep(1)
                continue

            # Convert to gray for stats
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            except Exception:
                gray = frame

            print(f"Frame captured. Shape: {gray.shape} | Mean Temp Value: {gray.mean():.2f}")
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nStopped by user.")

    cap.release()

if __name__ == "__main__":
    main()
