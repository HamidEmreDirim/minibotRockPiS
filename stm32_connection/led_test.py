import serial
import time
import sys

# --- CONFIGURATION ---
SERIAL_PORT = "/dev/stm32"
BAUD_RATE = 115200

def main():
    try:
        # Open Serial Port
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"--- CONNECTED TO {SERIAL_PORT} ---")
        print("Firmware Mode: Software PWM (TIM4)")
        print("Protocol: 'cam_led:XX' (No braces)")
        print("Type 0-100 to change brightness. Type 'q' to quit.\n")

    except Exception as e:
        print(f"Error: {e}")
        print("Did you run 'sudo systemctl stop robot_bridge.service'?")
        sys.exit(1)

    try:
        while True:
            val = input("Enter Brightness (0-100) > ")
            
            if val.lower() in ['q', 'quit', 'exit']:
                break
            
            # --- FIX: New Format based on your C code ---
            # Your code: sscanf(line, "cam_led:%d", &cam_val)
            # It does NOT look for '{' anymore.
            command = f"cam_led:{val}\n"
            
            # Send
            ser.write(command.encode('utf-8'))
            print(f"Sent: {repr(command)}")

            # Read Debug Response (if any)
            time.sleep(0.05) # Wait 50ms for STM32 to process
            while ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"Rx: {line}")

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == "__main__":
    main()
