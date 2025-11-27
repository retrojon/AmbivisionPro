import serial, time, struct, threading

PORT = "COM10"
BAUD = 115200
MAGIC = b"\xAA\x55"

# Reed-Solomon parameters
RS_NPAR = 10
RS_DATA_LEN = 64                  # bytes before parity
HEADER_LEN = 3                    # 2 bytes seq + 1 byte payload_len
MAX_USER = RS_DATA_LEN - HEADER_LEN  # 61 bytes max user payload

try:
    from reedsolo import RSCodec
    rs = RSCodec(RS_NPAR)
    print(f"[WIN] Reed-Solomon ENABLED ({RS_NPAR} parity bytes, {RS_DATA_LEN}-byte data block)")
except ImportError:
    rs = None
    print("[WIN] reedsolo not installed, FEC DISABLED (this must match Linux!)")


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
    return rs.encode(inner)  # -> data+parity


def fec_decode(block: bytes):
    """Return (decoded_inner_bytes, ok: bool)"""
    if rs is None:
        # no FEC, just pass through
        return block, True
    try:
        res = rs.decode(block)
        # different reedsolo versions return different shapes; use index 0 if it's a tuple/list
        if isinstance(res, (tuple, list)):
            decoded = res[0]
        else:
            decoded = res  # some versions return just bytes
        return decoded, True
    except Exception as e:
        print(f"[WIN] RS decode error: {e}")
        return b"", False


def build_frame(seq: int, user_payload: bytes) -> bytes:
    # clamp payload length
    if len(user_payload) > MAX_USER:
        user_payload = user_payload[:MAX_USER]

    # build fixed-size inner block
    inner = bytearray(RS_DATA_LEN)
    struct.pack_into(">H", inner, 0, seq & 0xFFFF)
    inner[2] = len(user_payload)
    inner[3:3+len(user_payload)] = user_payload
    # the rest stays as zero padding

    rs_block = fec_encode(bytes(inner))          # len = RS_DATA_LEN + RS_NPAR (if FEC enabled)
    length = struct.pack(">H", len(rs_block))
    crc = struct.pack(">H", crc16(rs_block))

    return MAGIC + length + rs_block + crc


def parse_frames(buf: bytearray):
    frames = []
    while True:
        idx = buf.find(MAGIC)
        if idx < 0:
            # keep at most last 3 bytes in case of partial MAGIC
            if len(buf) > 3:
                del buf[:-3]
            return frames

        if idx > 0:
            del buf[:idx]

        if len(buf) < 4:
            return frames

        length = struct.unpack(">H", buf[2:4])[0]
        total = 4 + length + 2  # header + payload + crc
        if len(buf) < total:
            return frames

        rs_block = bytes(buf[4:4 + length])
        recv_crc = struct.unpack(">H", buf[4 + length:4 + length + 2])[0]
        del buf[:total]

        if crc16(rs_block) != recv_crc:
            print("[WIN] BAD CRC, dropping frame")
            continue

        inner, ok = fec_decode(rs_block)
        if not ok or len(inner) < HEADER_LEN:
            print("[WIN] FEC failed or inner too short, dropping frame")
            continue

        seq = struct.unpack(">H", inner[:2])[0]
        plen = inner[2]
        if plen > MAX_USER:
            print(f"[WIN] Invalid payload length {plen}, dropping frame")
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
                print(f"[WIN RX] seq={seq:05d} data={payload!r}")
        except Exception as e:
            print("[WIN] Reader error:", e)
            return


def main():
    print(f"[WIN] Opening {PORT}")
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    t = threading.Thread(target=reader, args=(ser,), daemon=True)
    t.start()

    seq = 0
    try:
        while True:
            msg = f"WIN_HELLO_{seq}".encode()
            frame = build_frame(seq, msg)
            ser.write(frame)
            print(f"[WIN TX] seq={seq:05d} data={msg!r}")
            seq = (seq + 1) & 0xFFFF
            time.sleep(1.0)
    finally:
        ser.close()
        print("[WIN] Closed serial")


if __name__ == "__main__":
    main()
