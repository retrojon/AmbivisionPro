#!/usr/bin/env python3
import serial, time, sys

# ---------------- CONFIG ----------------
RFCOMM_PORT = "COM10"
BAUDRATE = 921600
HANDSHAKE_PKT_HEX = "04ca07927d8fa3e91d94"

WAKEUP = bytes.fromhex("edfe")
WAKEUP_COUNT = 6
WAKEUP_DELAY = 0.02
POST_WAKE_DELAY = 0.04

NUM_LEDS = 115
SEGMENT_SIZE = 16
HEADER = bytes.fromhex("abcd11")

# ---------------- UTILS ----------------
def open_port(port, baud):
    try:
        ser = serial.Serial(port, baud, timeout=1.0)
        print(f"[+] Opened {port} @ {baud}")
        return ser
    except Exception as e:
        print("[!] Failed to open port:", e)
        sys.exit(1)

def send_wakeup(ser):
    for _ in range(WAKEUP_COUNT):
        ser.write(WAKEUP)
        ser.flush()
        time.sleep(WAKEUP_DELAY)
    time.sleep(POST_WAKE_DELAY)

def send_handshake(ser, max_retries=20):
    pkt = bytes.fromhex(HANDSHAKE_PKT_HEX)
    for attempt in range(max_retries):
        print(f"[>] Sending handshake attempt {attempt+1}")
        ser.write(pkt)
        ser.flush()
        resp = ser.read(128)
        if resp and len(resp) >= 4:
            print("[<] Handshake resp:", resp.hex())
            return resp
        time.sleep(0.1)
    print("[!] Handshake failed after retries")
    return None

# BGR565 little-endian
def bgr565_le(r, g, b):
    v = ((b & 0xF8) << 8) | ((g & 0xFC) << 3) | (r >> 3)
    return bytes([v & 0xFF, v >> 8])

def crc16_ccitt(data, init=0xFFFF, poly=0x1021):
    crc = init
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) & 0xFFFF) ^ poly
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF

def build_segment_packet(segment_colors):
    payload = bytearray()
    for r, g, b in segment_colors:
        payload += bgr565_le(r, g, b)
    body = bytes([len(payload)]) + payload
    crc = crc16_ccitt(payload)
    pkt = HEADER + body + bytes([crc >> 8, crc & 0xFF])
    return pkt

# ---------------- DYNAMIC INPUT ----------------
def send_static_color(ser, handshake_resp, r, g, b):
    token = handshake_resp[3:]
    colors = [(r, g, b)] * NUM_LEDS
    for seg_start in range(0, NUM_LEDS, SEGMENT_SIZE):
        seg_colors = colors[seg_start:seg_start+SEGMENT_SIZE]
        pkt = build_segment_packet(seg_colors)
        ser.write(pkt)
        ser.flush()
        time.sleep(0.004)
    # reinforce token
    capture_pkt = bytes([0x04, 0xca, 0x07, 0x01]) + token
    ser.write(capture_pkt)
    ser.flush()
    print(f"[+] Sent static color ({r}, {g}, {b})")

# ---------------- MAIN ----------------
def main():
    ser = open_port(RFCOMM_PORT, BAUDRATE)
    send_wakeup(ser)
    handshake_resp = send_handshake(ser, max_retries=20)
    if not handshake_resp:
        ser.close()
        return

    print("[+] Enter RGB values (0-252) separated by spaces, or 'q' to quit")
    try:
        while True:
            inp = input("RGB> ").strip()
            if inp.lower() in ("q", "quit"):
                break
            try:
                r, g, b = map(int, inp.split())
                r, g, b = max(0, min(252, r)), max(0, min(252, g)), max(0, min(252, b))
                send_static_color(ser, handshake_resp, r, g, b)
            except:
                print("[!] Invalid input. Example: 252 0 0")
    except KeyboardInterrupt:
        pass

    ser.close()
    print("[+] Done")

if __name__ == "__main__":
    main()
