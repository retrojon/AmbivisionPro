#!/usr/bin/env python3
import serial, time, struct, threading

DEV = "/dev/ttyS0"
BAUD = 115200
MAGIC = b"\xAA\x55"

RS_NPAR = 10
RS_DATA_LEN = 64
HEADER_LEN = 3
MAX_USER = RS_DATA_LEN - HEADER_LEN  # 61 bytes

try:
    from reedsolo import RSCodec
    rs = RSCodec(RS_NPAR)
    print(f"[LINUX] Reed-Solomon ENABLED ({RS_NPAR} parity bytes, {RS_DATA_LEN}-byte data block)")
except ImportError:
    rs = None
    print("[LINUX] reedsolo not installed, FEC DISABLED (this must match Windows!)")


def crc16(data, poly=0x1021, init=0xFFFF):
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


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


def build_frame(seq: int, user_payload: bytes) -> bytes:
    if len(user_payload) > MAX_USER:
        user_payload = user_payload[:MAX_USER]

    inner = bytearray(RS_DATA_LEN)
    struct.pack_into(">H", inner, 0, seq & 0xFFFF)
    inner[2] = len(user_payload)
    inner[3:3+len(user_payload)] = user_payload

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

        user_data = inner[3:3+plen]
        frames.append((seq, user_data))


def reader(ser):
    buf = bytearray()
    while True:
        try:
            data = ser.read(512)
            if not data:
                continue
            buf.extend(data)
            for seq, payload in parse_frames(buf):
                print(f"[LINUX RX] seq={seq:05d} data={payload!r}")
        except Exception as e:
            print("[LINUX] Reader error:", e)
            return


def main():
    print(f"[LINUX] Opening {DEV}")
    ser = serial.Serial(DEV, BAUD, timeout=0.2)
    t = threading.Thread(target=reader, args=(ser,), daemon=True)
    t.start()

    seq = 0
    try:
        while True:
            msg = f"LINUX_HELLO_{seq}".encode()
            frame = build_frame(seq, msg)
            ser.write(frame)
            print(f"[LINUX TX] seq={seq:05d} data={msg!r}")
            seq = (seq + 1) & 0xFFFF
            time.sleep(1.0)
    finally:
        ser.close()
        print("[LINUX] Closed serial")


if __name__ == "__main__":
    main()

