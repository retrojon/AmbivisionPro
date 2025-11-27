#!/usr/bin/env python3
import serial
import socket
import struct
import threading
import time
import sys

# ---------------- CONFIG ----------------
DEV = "/dev/ttyS0"      # HC-05/06 UART on Ambi box
BAUD = 115200

SSH_HOST = "127.0.0.1"
SSH_PORT = 22           # local sshd

MAGIC = b"\xAA\x55"

RS_NPAR = 10
RS_DATA_LEN = 64
HEADER_LEN = 3
MAX_USER = RS_DATA_LEN - HEADER_LEN

TYPE_DATA = 0x01
TYPE_TURN = 0x02

MY_ID = 1           # Linux = 1
OTHER_ID = 0

MAX_FRAMES_PER_TURN = 50
FRAME_TX_DELAY = 0.01


# ---------------- Reed–Solomon / CRC ----------------
try:
    from reedsolo import RSCodec
    rs = RSCodec(RS_NPAR)
    print(f"[LINUX] Reed-Solomon ENABLED ({RS_NPAR} parity bytes, {RS_DATA_LEN}-byte data block)")
except ImportError:
    rs = None
    print("[LINUX] reedsolo not installed, FEC DISABLED (must match Windows!)")


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


def fec_encode(inner: bytes) -> bytes:
    if rs is None:
        return inner
    return rs.encode(inner)


def fec_decode(block: bytes):
    if rs is None:
        return block, True
    try:
        res = rs.decode(block)
        if isinstance(res, (tuple, list)):
            decoded = res[0]
        else:
            decoded = res
        return decoded, True
    except Exception as e:
        print(f"[LINUX] RS decode error: {e}")
        return b"", False


# ---------------- Frame building / parsing ----------------
def build_frame(seq: int, user_payload: bytes) -> bytes:
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


def parse_frames(buf: bytearray):
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
        total = 4 + length + 2
        if len(buf) < total:
            return frames

        rs_block = bytes(buf[4:4 + length])
        recv_crc = struct.unpack(">H", buf[4 + length:4 + length + 2])[0]
        del buf[:total]

        if crc16(rs_block) != recv_crc:
            print("[LINUX] BAD CRC, dropping frame")
            continue

        inner, ok = fec_decode(rs_block)
        if not ok or len(inner) < HEADER_LEN:
            print("[LINUX] FEC failed or inner too short, dropping frame")
            continue

        seq = struct.unpack(">H", inner[:2])[0]
        plen = inner[2]
        if plen > MAX_USER:
            print(f"[LINUX] Invalid payload length {plen}, dropping frame")
            continue

        user_data = inner[3:3 + plen]
        frames.append((seq, user_data))


# ---------------- Half-duplex modem logic ----------------
class HalfDuplexModemLin:
    def __init__(self, ser, ssh_sock):
        self.ser = ser
        self.ssh_sock = ssh_sock

        self.seq_tx = 0

        self.tx_buffer = bytearray()
        self.tx_lock = threading.Lock()

        # Start with Windows' turn (0). We'll wait for a TURN.
        self.current_turn = OTHER_ID
        self.turn_lock = threading.Lock()

        self.stop_event = threading.Event()

    def have_turn(self):
        with self.turn_lock:
            return self.current_turn == MY_ID

    def give_turn(self):
        with self.turn_lock:
            self.current_turn = OTHER_ID
        payload = bytes([TYPE_TURN])
        frame = build_frame(self.seq_tx, payload)
        self.seq_tx = (self.seq_tx + 1) & 0xFFFF
        self.ser.write(frame)
        self.ser.flush()
        print("[LINUX TX] TURN → remote")
        time.sleep(FRAME_TX_DELAY)

    def on_turn_received(self):
        with self.turn_lock:
            self.current_turn = MY_ID
        print("[LINUX RX] TURN → now my turn")

    def feed_tx(self, data: bytes):
        if not data:
            return
        with self.tx_lock:
            self.tx_buffer.extend(data)

    def tx_thread(self):
        print("[LINUX] TX thread started")
        while not self.stop_event.is_set():
            if not self.have_turn():
                time.sleep(0.005)
                continue

            chunk = None
            with self.tx_lock:
                if self.tx_buffer:
                    chunk = self.tx_buffer[:MAX_USER - 1]
                    del self.tx_buffer[:len(chunk)]

            frames_this_turn = 0

            if chunk is None:
                # no data; quickly yield
                self.give_turn()
                time.sleep(0.01)
                continue

            while chunk is not None and frames_this_turn < MAX_FRAMES_PER_TURN:
                payload = bytes([TYPE_DATA]) + chunk
                frame = build_frame(self.seq_tx, payload)
                self.seq_tx = (self.seq_tx + 1) & 0xFFFF
                self.ser.write(frame)
                self.ser.flush()
                print(f"[LINUX TX] DATA len={len(chunk)}")
                frames_this_turn += 1
                time.sleep(FRAME_TX_DELAY)

                with self.tx_lock:
                    if self.tx_buffer:
                        chunk = self.tx_buffer[:MAX_USER - 1]
                        del self.tx_buffer[:len(chunk)]
                    else:
                        chunk = None

            self.give_turn()

        print("[LINUX] TX thread stopping")

    def rx_thread(self):
        print("[LINUX] RX thread started")
        buf = bytearray()
        while not self.stop_event.is_set():
            try:
                data = self.ser.read(512)
                if not data:
                    time.sleep(0.005)
                    continue
                buf.extend(data)
                for seq, user_payload in parse_frames(buf):
                    if not user_payload:
                        continue
                    ftype = user_payload[0]
                    fdata = user_payload[1:]

                    if ftype == TYPE_TURN:
                        self.on_turn_received()
                    elif ftype == TYPE_DATA:
                        if fdata:
                            try:
                                self.ssh_sock.sendall(fdata)
                            except Exception as e:
                                print("[LINUX] SSH send error:", e)
                                self.stop_event.set()
                                break
                    else:
                        print(f"[LINUX RX] Unknown frame type {ftype:02x}")
            except Exception as e:
                print("[LINUX] RX error:", e)
                self.stop_event.set()
                break

        print("[LINUX] RX thread stopping")

    def ssh_reader_thread(self):
        print("[LINUX] SSH reader started")
        try:
            while not self.stop_event.is_set():
                data = self.ssh_sock.recv(4096)
                if not data:
                    print("[LINUX] SSH closed by remote")
                    self.stop_event.set()
                    break
                self.feed_tx(data)
        except Exception as e:
            print("[LINUX] SSH reader error:", e)
            self.stop_event.set()
        print("[LINUX] SSH reader stopping")

    def run(self):
        t_tx = threading.Thread(target=self.tx_thread, daemon=True)
        t_rx = threading.Thread(target=self.rx_thread, daemon=True)
        t_ssh = threading.Thread(target=self.ssh_reader_thread, daemon=True)

        t_tx.start()
        t_rx.start()
        t_ssh.start()

        while not self.stop_event.is_set():
            time.sleep(0.1)

        print("[LINUX] Modem shutting down")


# ---------------- Top-level ----------------
def main():
    print(f"[LINUX] Opening serial {DEV} @ {BAUD}")
    ser = serial.Serial(DEV, BAUD, timeout=0.2)

    print(f"[LINUX] Connecting to SSH {SSH_HOST}:{SSH_PORT}...")
    ssh_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ssh_sock.connect((SSH_HOST, SSH_PORT))
    ssh_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print("✅ SSH connected.")

    modem = HalfDuplexModemLin(ser, ssh_sock)

    try:
        modem.run()
    except KeyboardInterrupt:
        print("\n[LINUX] Interrupted.")
    finally:
        modem.stop_event.set()
        try:
            ssh_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        ssh_sock.close()
        ser.close()
        print("[LINUX] Closed serial & SSH")


if __name__ == "__main__":
    main()

