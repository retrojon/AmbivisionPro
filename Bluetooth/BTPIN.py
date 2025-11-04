def magiclink_pin(s: str) -> str:
    # expects s length >= 8 (e.g. "87651234")
    A = ord(s[1]); B = ord(s[2]); C = ord(s[3])
    D = ord(s[4]); E = ord(s[5]); F = ord(s[6]); G = ord(s[7])
    part1 = (A * C) ^ (G * B) ^ 0x01
    part2 = (D | (F << 3)) ^ E ^ 0x6D
    pinnum = (part1 + part2) % 10000
    return f"{pinnum:04d}"

# Examples:

print(magiclink_pin("87651234"))  # -> "XXXX"

