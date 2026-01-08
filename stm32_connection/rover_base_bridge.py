#!/usr/bin/env python3
import asyncio
import json
import time
import serial
import threading
import math
import logging
from websockets.asyncio.server import serve 
from websockets import broadcast
from websockets.exceptions import ConnectionClosed

# --- CONFIGURATION ---
SERIAL_PORT = '/dev/stm32'
BAUD_RATE = 115200

# Server Ports
WS_PORT_TELEMETRY = 8080   # Robot -> Base (Data)
WS_PORT_CONTROL = 8766     # Base -> Robot (Joystick + Mode)
WS_PORT_HEARTBEAT = 9000   # Ping/Pong

# Safety Timeouts (Seconds)
TIMEOUT_CONTROL = 0.5      # Stop if no joystick data for 0.5s
TIMEOUT_HEARTBEAT = 1.0    # Stop if no heartbeat ping for 1.0s

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("websockets.server")
logger.setLevel(logging.ERROR) 

# --- GLOBAL STATE & LOCKS ---
serial_lock = asyncio.Lock()
ser_connection = None  # Will be managed by the connection monitor

# Robot State
stm32_state = {
    "mode": "IDLE", 
    "x": 0, "y": 0, "z": 0, 
    "bat_v": 0.0, "bat_pct": 0, 
    "temp": 0.0, "hum": 0.0, "press": 0.0,
    "pm1": 0.0, "pm2p5": 0.0, "pm4": 0.0, "pm10": 0.0
}

# Control State
latest_control = {"v": 0.0, "w": 0.0}

# Watchdog Timestamps
last_control_msg_time = 0.0
last_heartbeat_ack_time = 0.0
current_latency_ms = 0

def parse_stm32_line(line):
    try:
        content = line.strip().strip("{}").strip()
        data = {}
        for part in content.split(','):
            if ':' in part:
                k, v = part.split(':', 1)
                try:
                    data[k.strip()] = float(v) if '.' in v else int(v)
                except ValueError:
                    data[k.strip()] = v.strip()
        return data
    except:
        return None

def serial_reader_thread():
    """ Runs forever. Reads if connected, sleeps if not. """
    global ser_connection, stm32_state
    print("[Serial] Reader Thread Started")
    
    while True:
        # 1. Check if we have a valid object
        current_ser = ser_connection
        if current_ser is None:
            time.sleep(0.1)
            continue

        try:
            # 2. Try reading
            if current_ser.in_waiting > 0:
                line = current_ser.readline().decode('utf-8', errors='ignore').strip()
                if not line: continue

                if "CMD_HELLO" in line:
                    # Write safely using the local reference
                    try:
                        current_ser.write(b"ACK_READY\n")
                    except: pass 
                    continue

                data = parse_stm32_line(line)
                if data:
                    for k, v in data.items():
                        if k in stm32_state:
                            stm32_state[k] = v
                            
        except Exception:
            # If reading fails (device disconnected), we do nothing here.
            # The 'write' operations or the monitor will detect the failure 
            # and set ser_connection to None.
            time.sleep(0.5)

async def monitor_connection_task():
    """ Automatically connects and reconnects to the Serial Port """
    global ser_connection
    print("[System] Connection Monitor Started")

    while True:
        # If disconnected, try to connect
        if ser_connection is None:
            try:
                print(f"[Serial] Connecting to {SERIAL_PORT}...")
                new_ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                time.sleep(2) # Wait for bootloader/reset
                
                # Send handshake
                new_ser.write(b"ACK_READY\n")
                
                # Update global object
                ser_connection = new_ser
                print("[Serial] Connected successfully!")
            except Exception as e:
                print(f"[Serial] Connection failed: {e}. Retrying in 2s...")
                await asyncio.sleep(2)
        
        await asyncio.sleep(1)

# --- WEBSOCKET HANDLERS ---

async def handle_telemetry(websocket):
    try:
        await websocket.wait_closed()
    except: pass

async def handle_control_wrapper(websocket):
    global latest_control, last_control_msg_time, ser_connection
    print(f"[Control] Client connected")
    
    try:
        async for message in websocket:
            last_control_msg_time = time.time()
            try:
                data = json.loads(message)

                # 1. Handle Mode Switching
                if "mode" in data:
                    mode_cmd = str(data["mode"]).upper()
                    print(f"[Control] Sending Mode: {mode_cmd}")
                    
                    async with serial_lock:
                        if ser_connection:
                            try:
                                ser_connection.write(f"{mode_cmd}\n".encode('utf-8'))
                            except OSError:
                                print("[Serial] Write Error (Mode). Closing connection.")
                                try: ser_connection.close()
                                except: pass
                                ser_connection = None # Trigger Reconnect

                # 2. Update Velocity
                if "v" in data or "w" in data:
                    latest_control["v"] = float(data.get("v", 0.0))
                    latest_control["w"] = float(data.get("w", 0.0))

            except Exception as e:
                print(f"JSON/Logic Error: {e}")

    except: pass
    finally:
        print("[Control] Client disconnected")

async def handle_heartbeat(websocket):
    global last_heartbeat_ack_time, current_latency_ms
    print("[Heartbeat] Client connected")
    try:
        while True:
            t_start = time.time()
            try:
                await websocket.send(json.dumps({"type": "ping", "ts": t_start}))
                resp = await asyncio.wait_for(websocket.recv(), 1.0)
                if json.loads(resp).get("type") == "ping":
                    last_heartbeat_ack_time = time.time()
                    current_latency_ms = int((time.time() - t_start) * 1000)
                    await websocket.send(json.dumps({"type": "rtt", "val": current_latency_ms}))
            except Exception:
                pass
            await asyncio.sleep(0.5)
    except: pass

async def control_loop_task():
    global ser_connection
    V_MAX = 1.0 
    W_MAX = 1.0 
    
    print("[Control Loop] Started")
    
    while True:
        now = time.time()
        is_control_fresh = (now - last_control_msg_time) < TIMEOUT_CONTROL
        is_heartbeat_fresh = (now - last_heartbeat_ack_time) < TIMEOUT_HEARTBEAT
        is_safe = is_control_fresh and is_heartbeat_fresh

        target_v = 0.0
        target_w = 0.0

        if is_safe:
            raw_v = latest_control["v"]
            raw_w = latest_control["w"]
            target_v = max(-1.0, min(1.0, -raw_v)) * V_MAX
            target_w = max(-1.0, min(1.0, -raw_w)) * W_MAX
            cmd = f"{{v={target_v:.2f}, w={target_w:.2f}}}\n"
        else:
            cmd = "{v=0.00, w=0.00}\n"

        # Attempt to write only if connected
        if ser_connection:
            try:
                async with serial_lock:
                    # Double check inside lock
                    if ser_connection: 
                        ser_connection.write(cmd.encode('utf-8'))
            except OSError:
                print("[Serial] Write Error (Loop). Closing connection.")
                try: ser_connection.close()
                except: pass
                ser_connection = None # Trigger Reconnect
            except Exception: pass

        await asyncio.sleep(0.05) 

async def broadcast_telemetry_task(server):
    while True:
        t = time.time()
        is_connected = (t - last_heartbeat_ack_time) < TIMEOUT_HEARTBEAT

        imu_str = f"{stm32_state['x']},{stm32_state['y']},{stm32_state['z']}"
        telemetry = {
            "type": "telemetry",
            "timestamp": int(t * 1000),
            "mode": stm32_state["mode"],
            "imu": imu_str,
            "acceleration": 1,
            "battery_pct": stm32_state["bat_pct"],
            "battery_v": stm32_state["bat_v"],
            "temperature_c": stm32_state["temp"],
            "humidity": stm32_state["hum"],
            "pressure": stm32_state["press"],
            "pm1": stm32_state["pm1"],
            "pm2p5": stm32_state["pm2p5"],
            "pm4": stm32_state["pm4"],
            "pm10": stm32_state["pm10"],
            "connection": {"connected": is_connected, "latency_ms": current_latency_ms}
        }
        target = [ws for ws in server.connections if ws.request.path == "/telemetry"]
        if target: broadcast(target, json.dumps(telemetry))
        await asyncio.sleep(0.1)

async def broadcast_gps_task(server):
    while True:
        t = time.time()
        gps = {
            "type": "gps", "timestamp": int(t*1000),
            "lat": 39.9334 + (0.0001 * math.sin(t/10)), "lon": 32.8597 + (0.0001 * math.cos(t/10)),
            "alt": 900, "status": 0, "service": 1, "position_covariance": [0.0]*9, "position_covariance_type": 0
        }
        target = [ws for ws in server.connections if ws.request.path == "/gps"]
        if target: broadcast(target, json.dumps(gps))
        await asyncio.sleep(1.0)

async def main():
    # Start the reader thread immediately (it will wait for connection)
    threading.Thread(target=serial_reader_thread, daemon=True).start()

    srv_telem = await serve(handle_telemetry, "0.0.0.0", WS_PORT_TELEMETRY)
    srv_ctrl = await serve(handle_control_wrapper, "0.0.0.0", WS_PORT_CONTROL)
    srv_ping = await serve(handle_heartbeat, "0.0.0.0", WS_PORT_HEARTBEAT)

    print(f"Bridge Started. Tel:{WS_PORT_TELEMETRY}, Ctrl:{WS_PORT_CONTROL}, HB:{WS_PORT_HEARTBEAT}")

    await asyncio.gather(
        monitor_connection_task(), # <--- New Task handles Reconnection
        control_loop_task(),
        broadcast_telemetry_task(srv_telem),
        broadcast_gps_task(srv_telem),
        srv_telem.serve_forever(),
        srv_ctrl.serve_forever(),
        srv_ping.serve_forever()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping...")
