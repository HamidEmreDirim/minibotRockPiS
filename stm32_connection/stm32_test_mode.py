import serial
import threading
import time
import sys
import random
# Configuration
SERIAL_PORT = '/dev/stm32'  # Ensure this matches your actual port
BAUD_RATE = 115200
CONTROL_RATE = 1  # 10Hz
# Global flags
control_active = False  
running = True          
def listen_for_data(ser):
    """
    Background thread: continuously prints data received FROM STM32.
    """
    while running:
        try:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                try:
                    # Clean up output
                    text = data.decode('utf-8', errors='ignore').strip()
                    if text:
                        print(f"\n[STM32]: {text}")
                except Exception:
                    pass
            time.sleep(0.01)
        except OSError:
            print("\n[Error] Serial port disconnected.")
            break
def send_control_loop(ser):
    """
    Background thread: Sends TANK CONTROL messages if control_active is True.
    """
    global control_active
    while running:
        if control_active:
            try:
                # Random tank drive values
                v = random.uniform(-1.0, 1.0) 
                w = random.uniform(-1.0, 1.0)
                
                # Format: {v=0.50, w=-0.10}
                msg = f"{{v={v:.2f}, w={w:.2f}}}\n"
                ser.write(msg.encode('utf-8'))
                
            except Exception as e:
                print(f"Error sending control: {e}")
                control_active = False
        
        time.sleep(CONTROL_RATE)
def main():
    global control_active, running, ser
    # 1. Connect
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"Connected to {SERIAL_PORT}")
    except serial.SerialException as e:
        print(f"Failed to connect: {e}")
        return
    # 2. Handshake Phase
    print("Waiting for Handshake...")
    # Send ACK repeatedly until we might see a response or just once.
    # The STM32 waits for ACK_READY.
    time.sleep(1)
    print("Sending ACK_READY...")
    ser.write("ACK_READY\n".encode('utf-8'))
    
    # 3. Start Threads
    t_listen = threading.Thread(target=listen_for_data, args=(ser,), daemon=True)
    t_listen.start()
    t_control = threading.Thread(target=send_control_loop, args=(ser,), daemon=True)
    t_control.start()
    print("-" * 50)
    print("COMMANDS:")
    print("  'ARMED' / 'ARMED+' -> Enable Motors & Random Control")
    print("  'ECO'              -> Enable Eco Mode")
    print("  'IDLE'             -> Disable Motors")
    print("  'EXIT'             -> Quit")
    print("-" * 50)
    # 4. Main Input Loop
    try:
        while True:
            user_input = input().strip().upper()
            if user_input == "EXIT":
                running = False
                break
            
            elif user_input in ["ARMED", "ARMED+", "ECO", "IDLE"]:
                # SEND THE COMMAND TO STM32
                print(f"\n>>> Sending Command: {user_input}")
                ser.write((user_input + "\n").encode('utf-8'))
                
                # Logic for python side control loop
                if user_input in ["ARMED", "ARMED+", "ECO"]:
                    if not control_active:
                        print(">>> Starting Control Loop...")
                        control_active = True
                else:
                    if control_active:
                        print(">>> Stopping Control Loop...")
                        control_active = False
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        running = False
        if 'ser' in locals() and ser.is_open:
            ser.close()
if __name__ == "__main__":
    main()
