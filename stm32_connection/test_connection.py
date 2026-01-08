import serial
import threading
import sys
import time

# Configuration
SERIAL_PORT = '/dev/stm32'
BAUD_RATE = 115200  # Virtual COM ports usually ignore this, but 115200 is standard

def listen_for_data(ser):
    """
    Runs in a background thread to continuously check for incoming data
    from the STM32 and print it to the terminal.
    """
    try:
        while True:
            # Check if there is data waiting in the serial buffer
            if ser.in_waiting > 0:
                # Read all available bytes
                data = ser.read(ser.in_waiting)
                
                # Decode bytes to string (assuming UTF-8/ASCII) and print
                try:
                    # using sys.stdout.write to avoid adding extra newlines automatically
                    sys.stdout.write(data.decode('utf-8'))
                    sys.stdout.flush()
                except UnicodeDecodeError:
                    # Fallback for non-text data
                    print(f"\n[Raw Bytes]: {data}")
            
            # Small sleep to prevent 100% CPU usage in the thread loop
            time.sleep(0.01)
            
    except OSError:
        print("\n[Error] Serial port disconnected or lost.")
        sys.exit(1)

def main():
    try:
        # Initialize Serial Connection
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"Connected to {SERIAL_PORT} at {BAUD_RATE} baud.")
        print("Type a message and press ENTER to send to STM32.")
        print("Press Ctrl+C to exit.\n")
        print("-" * 40)

        # Start the listening thread
        listener_thread = threading.Thread(target=listen_for_data, args=(ser,), daemon=True)
        listener_thread.start()

        # Main loop: Handle sending data
        while True:
            # Get input from user (blocking)
            user_input = input()
            
            # Append a newline character because input() strips it, 
            # and terminals usually expect \n or \r to process a command.
            message_to_send = user_input + '\n'
            
            # Send encoded bytes to STM32
            ser.write(message_to_send.encode('utf-8'))

    except serial.SerialException as e:
        print(f"Could not open serial port {SERIAL_PORT}: {e}")
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == "__main__":
    main()
