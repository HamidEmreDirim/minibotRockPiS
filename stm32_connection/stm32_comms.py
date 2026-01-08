import serial
import time
import sys
import random

# Configuration
SERIAL_PORT = '/dev/stm32'
BAUD_RATE = 115200
RETRY_DELAY = 5       # Seconds to wait before retrying connection
SEND_INTERVAL = 0.5   # 100 ms interval for sending data

def main():
    while True:
        ser = None
        try:
            # -----------------------------------------------------
            # 1. Attempt Connection
            # -----------------------------------------------------
            print(f"Attempting to connect to {SERIAL_PORT}...")
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
            print(f"Connected to {SERIAL_PORT}!")

            # -----------------------------------------------------
            # 2. Send Initial ACK
            # -----------------------------------------------------
            time.sleep(2)
            ser.write("ACK_READY\n".encode('utf-8'))
            print("Sent: ACK_READY")

            # Initialize timer for sending data
            last_send_time = time.time()

            # -----------------------------------------------------
            # 3. Main Operational Loop (Read + Write)
            # -----------------------------------------------------
            while True:
                # --- TASK A: Check if it's time to SEND data (Every 100ms) ---
                current_time = time.time()
                if (current_time - last_send_time) >= SEND_INTERVAL:
                    
                    # Generate random values between -1.0 and 1.0
                    v = random.uniform(-1.0, 1.0)
                    w = random.uniform(-1.0, 1.0)
                    
                    # Format the message exactly as requested
                    # Note: You requested 'rb=0' twice. I kept it exactly as you asked.
                    msg = f"{{v={v:.2f}, w={w:.2f}, lb=0, rb=0, rb=0, rt=0}}\n"
                    
                    ser.write(msg.encode('utf-8'))
                    
                    # Reset the timer
                    last_send_time = current_time


                # --- TASK B: Check for INCOMING data (Always) ---
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    try:
                        # Log to system journal
                        sys.stdout.write(data.decode('utf-8'))
                        sys.stdout.flush()
                    except UnicodeDecodeError:
                        pass # Ignore garbled bits
                
                # --- Sleep briefly to prevent 100% CPU usage ---
                time.sleep(0.01)

        except (serial.SerialException, OSError) as e:
            # -----------------------------------------------------
            # 4. Failure Handling
            # -----------------------------------------------------
            print(f"\n[Error] Connection failed: {e}")
            print(f"Retrying in {RETRY_DELAY} seconds...")
            
            if ser and ser.is_open:
                try:
                    ser.close()
                except:
                    pass
            
            time.sleep(RETRY_DELAY)

        except KeyboardInterrupt:
            print("\nStopping service...")
            break

if __name__ == "__main__":
    main()
