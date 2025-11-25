import serial
import socket
import struct
import threading
import sys
import time

# ADJUST THIS TO YOUR HC-05 PORT
SERIAL_PORT = "COM5"
BAUD        = 115200

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 2222
MAX_PAYLOAD = 1024


class FrameCodec:
    MAGIC = b"SSHT"

    def __init__(self):
        self.buf = bytearray()

    def pack(self, payload: bytes) -> bytes:
        if len(payload) > 0xFFFF:
            raise ValueError("payload too long")
        return self.MAGIC + struct.pack("!H", len(payload)) + payload

    def feed(self, data: bytes):
        frames = []
        self.buf.extend(data)

        while True:
            idx = self.buf.find(self.MAGIC)
            if idx < 0:
                if len(self.buf) > 3:
                    self.buf = self.buf[-3:]
                break

            if idx > 0:
                del self.buf[:idx]

            if len(self.buf) < 6:
                break

            length = struct.unpack("!H", self.buf[4:6])[0]
            if length > 65535:
                del self.buf[0]
                continue

            if len(self.buf) < 6 + length:
                break

            payload = bytes(self.buf[6:6 + length])
            del self.buf[:6 + length]
            frames.append(payload)

        return frames


def bridge_socket_to_serial(sock, ser, codec, stop_event):
    """TCP → serial"""
    try:
        while not stop_event.is_set():
            data = sock.recv(4096)
            if not data:
                # client closed
                stop_event.set()
                break

            offset = 0
            n = len(data)
            while offset < n:
                chunk = data[offset:offset + MAX_PAYLOAD]
                frame = codec.pack(chunk)
                ser.write(frame)
                offset += len(chunk)
    except Exception as e:
        print("[WIN] socket->serial error:", e)
    finally:
        stop_event.set()


def bridge_serial_to_socket(ser, sock, codec, stop_event):
    """serial → TCP"""
    try:
        while not stop_event.is_set():
            # small sleep to avoid hammering CPU
            time.sleep(0.01)
            n = ser.in_waiting
            if n <= 0:
                continue
            data = ser.read(n)
            if not data:
                continue

            for payload in codec.feed(data):
                try:
                    sock.sendall(payload)
                except Exception as e:
                    print("[WIN] serial->socket error:", e)
                    stop_event.set()
                    break
    finally:
        stop_event.set()


def handle_client(client_sock, ser):
    print("[WIN] SSH client connected from:", client_sock.getpeername())
    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    codec = FrameCodec()
    stop_event = threading.Event()

    t1 = threading.Thread(
        target=bridge_socket_to_serial,
        args=(client_sock, ser, codec, stop_event),
        daemon=True,
    )
    t2 = threading.Thread(
        target=bridge_serial_to_socket,
        args=(ser, client_sock, codec, stop_event),
        daemon=True,
    )

    t1.start()
    t2.start()

    # wait until either side stops
    while not stop_event.is_set():
        time.sleep(0.1)

    print("[WIN] Closing SSH client.")
    try:
        client_sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    client_sock.close()


def main():
    print("[WIN] Opening serial {} @ {}...".format(SERIAL_PORT, BAUD))
    ser = serial.Serial(
        SERIAL_PORT,
        BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0,
    )

    print("✅ SSH tunnel WINDOWS side ready.")
    print("   Serial: {} @ {}".format(SERIAL_PORT, BAUD))
    print("   Listening for SSH on {}:{} (connect with ssh -p {} user@localhost)".format(
        LISTEN_HOST, LISTEN_PORT, LISTEN_PORT))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(1)

    try:
        while True:
            print("[WIN] Waiting for SSH client...")
            client, addr = srv.accept()
            try:
                handle_client(client, ser)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print("[WIN] Error in client handler:", e)
    except KeyboardInterrupt:
        print("\n[WIN] Interrupted, exiting.")
    finally:
        srv.close()
        ser.close()


if __name__ == "__main__":
    main()
