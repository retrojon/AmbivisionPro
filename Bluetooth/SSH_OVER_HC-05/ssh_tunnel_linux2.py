#!/usr/bin/env python3
import serial
import socket
import select
import struct
import sys
import time

SERIAL_DEV = "/dev/ttyS0"   # your HC-05 UART
BAUD       = 115200
SSH_HOST   = "127.0.0.1"
SSH_PORT   = 22
MAX_PAYLOAD = 128

class FrameCodec:
    MAGIC = b"SSHT"

    def __init__(self):
        self.buf = bytearray()

    def pack(self, payload: bytes) -> bytes:
        if len(payload) > 0xFFFF:
            raise ValueError("payload too long")
        return self.MAGIC + struct.pack("!H", len(payload)) + payload

    def feed(self, data: bytes):
        """Feed raw bytes, yield complete payload frames."""
        frames = []
        self.buf.extend(data)

        while True:
            # find MAGIC
            idx = self.buf.find(self.MAGIC)
            if idx < 0:
                # keep at most last 3 bytes (in case partial MAGIC)
                if len(self.buf) > 3:
                    self.buf = self.buf[-3:]
                break

            if idx > 0:
                del self.buf[:idx]

            if len(self.buf) < 6:
                # not enough for header
                break

            length = struct.unpack("!H", self.buf[4:6])[0]

            # simple sanity check
            if length > 65535:
                # desync, drop first byte and rescan
                del self.buf[0]
                continue

            if len(self.buf) < 6 + length:
                # wait for full payload
                break

            payload = bytes(self.buf[6:6 + length])
            del self.buf[:6 + length]
            frames.append(payload)

        return frames


def main():
    print("[LINUX] Opening serial {} @ {}...".format(SERIAL_DEV, BAUD))
    ser = serial.Serial(
        SERIAL_DEV,
        BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0,          # non-blocking
    )

    codec = FrameCodec()
    ssh_sock = None
    last_rx = time.time()

    print("✅ SSH tunnel LINUX side ready.")
    print("   Serial: {} @ {}".format(SERIAL_DEV, BAUD))
    print("   Will connect to SSH {}:{} on first incoming frame.".format(SSH_HOST, SSH_PORT))

    try:
        while True:
            rlist = [ser]
            if ssh_sock is not None:
                rlist.append(ssh_sock)

            readable, _, _ = select.select(rlist, [], [], 0.1)

            # SERIAL → SSH
            if ser in readable:
                data = ser.read(4096)
                if data:
                    last_rx = time.time()
                    for payload in codec.feed(data):
                        if ssh_sock is None:
                            # open SSH connection on first frame
                            print("[LINUX] Opening SSH connection to {}:{}...".format(SSH_HOST, SSH_PORT))
                            ssh_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            ssh_sock.connect((SSH_HOST, SSH_PORT))
                            ssh_sock.setblocking(False)
                            print("✅ SSH connected.")

                        try:
                            ssh_sock.sendall(payload)
                        except (OSError, BrokenPipeError) as e:
                            print("[LINUX] SSH send error:", e)
                            ssh_sock.close()
                            ssh_sock = None

            # SSH → SERIAL
            if ssh_sock is not None and ssh_sock in readable:
                try:
                    data = ssh_sock.recv(4096)
                except BlockingIOError:
                    data = b""
                if not data:
                    print("[LINUX] SSH connection closed by remote.")
                    ssh_sock.close()
                    ssh_sock = None
                else:
                    # chunk if large
                    offset = 0
                    n = len(data)
                    while offset < n:
                        chunk = data[offset:offset + MAX_PAYLOAD]
                        frame = codec.pack(chunk)
                        ser.write(frame)
                        offset += len(chunk)

    except KeyboardInterrupt:
        print("\n[LINUX] Interrupted, exiting.")
    finally:
        if ssh_sock is not None:
            ssh_sock.close()
        ser.close()


if __name__ == "__main__":
    main()

