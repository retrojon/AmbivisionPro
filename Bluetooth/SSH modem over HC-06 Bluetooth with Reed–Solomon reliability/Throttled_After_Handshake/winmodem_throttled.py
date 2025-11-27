#!/usr/bin/env python3
import serial
import socket
import struct
import threading
import time

# ---------------- CONFIG ----------------
COM = "COM10"
BAUD = 115200

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 2222  # ssh -p 2222 root@localhost

MAGIC = b"\xAA\x55"

RS_NPAR = 10
RS_DATA_LEN = 64
HEADER_LEN = 3
MAX_USER = RS_DATA_LEN - HEADER_LEN

TYPE_DATA = 0x01
TYPE_TURN = 0x02

MY_ID = 0          # Windows
OTHER_ID = 1       # Linux

MAX_FRAMES_PER_TURN = 50
FRAME_TX_DELAY = 0.01

# Throttle BOTH directions, but after first REAL DATA
THROTTLE_CHUNK = 32
THROTTLE_DELAY = 0.10


# ---------------- Reed–Solomon / CRC ----------------
try:
    from reedsolo import RSCodec
    rs = RSCodec(RS_NPAR)
    print(f"[WIN] Reed-Solomon ENABLED ({RS_NPAR} parity bytes)")
except ImportError:
    rs = None
    print("[WIN] FEC disabled (reedsolo module missing)")


def crc16(data, poly=0x1021, init=0xFFFF):
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def fec_encode(inner):
    if rs is None:
        return inner
    return rs.encode(inner)


def fec_decode(block):
    if rs is None:
        return block, True
    try:
        res = rs.decode(block)
        decoded = res[0] if isinstance(res, (tuple, list)) else res
        return decoded, True
    except Exception:
        return b"", False


def build_frame(seq, user_payload):
    if len(user_payload) > MAX_USER:
        user_payload = user_payload[:MAX_USER]

    inner = bytearray(RS_DATA_LEN)
    struct.pack_into(">H", inner, 0, seq & 0xFFFF)
    inner[2] = len(user_payload)
    inner[3:3 + len(user_payload)] = user_payload

    rs_block = fec_encode(bytes(inner))
    length = struct.pack(">H", len(rs_block))
    crc = struct.pack(">H", crc16(rs_block))

    return MAGIC + length + rs_block + crc


def parse_frames(buf):
    frames = []
    while True:
        idx = buf.find(MAGIC)
        if idx < 0:
            if len(buf) > 3:
                del buf[:-3]
            return frames

        if idx > 0:
            del buf[:idx]

        if len(buf) < 4:
            return frames

        length = struct.unpack(">H", buf[2:4])[0]
        total_len = 4 + length + 2
        if len(buf) < total_len:
            return frames

        rs_block = bytes(buf[4:4 + length])
        recv_crc = struct.unpack(">H", buf[4 + length:4 + length + 2])[0]

        del buf[:total_len]

        if crc16(rs_block) != recv_crc:
            continue

        inner, ok = fec_decode(rs_block)
        if not ok or len(inner) < HEADER_LEN:
            continue

        plen = inner[2]
        if plen > MAX_USER:
            continue

        seq = struct.unpack(">H", inner[:2])[0]
        user_data = inner[3:3 + plen]
        frames.append((seq, user_data))


# ---------------- MODEM (WINDOWS) ----------------
class WinModem:
    def __init__(self, ser, client):
        self.ser = ser
        self.client = client

        self.seq_tx = 0
        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()

        self.turn_lock = threading.Lock()
        self.current_turn = MY_ID  # Windows starts

        self.stop_event = threading.Event()
        self.throttle = False  # OFF until first REAL DATA

    def have_turn(self):
        with self.turn_lock:
            return self.current_turn == MY_ID

    def give_turn(self):
        with self.turn_lock:
            self.current_turn = OTHER_ID

        frame = build_frame(self.seq_tx, bytes([TYPE_TURN]))
        self.seq_tx = (self.seq_tx + 1) & 0xFFFF

        self.ser.write(frame)
        self.ser.flush()
        time.sleep(FRAME_TX_DELAY)

    def on_turn_received(self):
        with self.turn_lock:
            self.current_turn = MY_ID

    def feed_tx(self, data):
        if not data:
            return
        with self.tx_lock:
            self.tx_buffer.extend(data)

    # -------- TX thread --------
    def tx_thread(self):
        print("[WIN] TX thread start")
        while not self.stop_event.is_set():

            if not self.have_turn():
                time.sleep(0.005)
                continue

            chunk = None
            with self.tx_lock:
                if self.tx_buffer:
                    chunk = self.tx_buffer[:MAX_USER - 1]
                    del self.tx_buffer[:len(chunk)]

            if not chunk:
                self.give_turn()
                time.sleep(0.01)
                continue

            frames = 0
            while chunk and frames < MAX_FRAMES_PER_TURN:
                payload = bytes([TYPE_DATA]) + chunk
                frame = build_frame(self.seq_tx, payload)
                self.seq_tx = (self.seq_tx + 1) & 0xFFFF

                self.ser.write(frame)
                self.ser.flush()
                frames += 1
                time.sleep(FRAME_TX_DELAY)

                with self.tx_lock:
                    if self.tx_buffer:
                        chunk = self.tx_buffer[:MAX_USER - 1]
                        del self.tx_buffer[:len(chunk)]
                    else:
                        chunk = None

            self.give_turn()

        print("[WIN] TX thread stop")

    # -------- RX thread --------
    def rx_thread(self):
        print("[WIN] RX thread start")
        buf = bytearray()

        while not self.stop_event.is_set():
            try:
                data = self.ser.read(512)
                if not data:
                    time.sleep(0.005)
                    continue

                buf.extend(data)

                for seq, payload in parse_frames(buf):
                    if not payload:
                        continue

                    ftype = payload[0]
                    fdata = payload[1:]

                    if ftype == TYPE_TURN:
                        self.on_turn_received()

                    elif ftype == TYPE_DATA:
                        if len(fdata) > 0 and not self.throttle:
                            print("[WIN] First REAL DATA received → throttle ON")
                            self.throttle = True

                        if fdata:
                            try:
                                self.client.sendall(fdata)
                            except Exception as e:
                                print("[WIN] Client send error:", e)
                                self.throttle = False
                                self.stop_event.set()
                                break

            except Exception as e:
                print("[WIN] RX error:", e)
                self.stop_event.set()
                break

        print("[WIN] RX thread stop")

    # -------- SSH client reader (PuTTY → modem) --------
    def client_reader(self):
        print("[WIN] Client reader start")
        try:
            while not self.stop_event.is_set():
                data = self.client.recv(THROTTLE_CHUNK)
                if not data:
                    print("[WIN] Client disconnected → throttle OFF")
                    self.throttle = False
                    self.stop_event.set()
                    break

                if self.throttle:
                    time.sleep(THROTTLE_DELAY)

                self.feed_tx(data)

        except Exception as e:
            print("[WIN] Client reader error:", e)
            self.stop_event.set()

        print("[WIN] Client reader stop")

    def run(self):
        # full reset per SSH client session
        self.stop_event.clear()
        self.throttle = False
        self.seq_tx = 0
        self.tx_buffer = bytearray()
        with self.turn_lock:
            self.current_turn = MY_ID

        t_tx = threading.Thread(target=self.tx_thread, daemon=True)
        t_rx = threading.Thread(target=self.rx_thread, daemon=True)
        t_cr = threading.Thread(target=self.client_reader, daemon=True)

        t_tx.start()
        t_rx.start()
        t_cr.start()

        while not self.stop_event.is_set():
            time.sleep(0.1)

        print("[WIN] Modem shutting down")


# ---------------- SUPERVISOR ----------------
def main():
    print(f"[WIN] Opening serial {COM} @ {BAUD}")
    ser = serial.Serial(COM, BAUD, timeout=0.2)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print(f"[WIN] Listening on {LISTEN_HOST}:{LISTEN_PORT}")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(1)

    while True:
        print("[WIN] Waiting for SSH client...")
        client, addr = server.accept()
        print(f"[WIN] New SSH client connected from {addr} → throttle OFF")

        modem = WinModem(ser, client)

        try:
            modem.run()
        except Exception as e:
            print("[WIN] modem.run error:", e)

        modem.stop_event.set()
        try:
            client.close()
        except Exception:
            pass

        time.sleep(0.5)


if __name__ == "__main__":
    main()
