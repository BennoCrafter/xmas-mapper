#!/usr/bin/env python3
"""
Decrypts Govee Bluetooth traffic from a PacketLogger capture (.pklg).

Capturing: quit the Govee app, start a PacketLogger trace, then open the app
(so the e7 01 / e7 02 handshake is included), then perform your actions.

  pip install cryptography
  python main.py decode capture.pklg
  python main.py decode capture.pklg --no-keepalive     # hide aa packets
  python main.py decode capture.pklg --only a4 33        # only certain commands
"""

import argparse
import datetime
import struct
from collections import defaultdict

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PSK = b"MakingLifeSmarte"
WRITE_SUFFIX = bytes.fromhex("112b")  # ...2b11 (UUID little endian, last 2 bytes)
NOTIFY_SUFFIX = bytes.fromhex("102b")  # ...2b10


# ---------- Crypto (same as the control script) ----------
def rc4(key, data):
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for c in data:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out.append(c ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


def decrypt(key, data):
    n = len(data) // 16 * 16
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(data[:n]) + rc4(key, data[n:])


def checksum_ok(p):
    x = 0
    for b in p[:-1]:
        x ^= b
    return len(p) > 1 and x == p[-1]


# ---------- Reading PacketLogger ----------
def read_pklg(path):
    d = open(path, "rb").read()
    i = 0
    while i + 4 <= len(d):
        (n,) = struct.unpack("<I", d[i : i + 4])
        rec = d[i + 4 : i + 4 + n]
        i += 4 + n
        if len(rec) < 9:
            break
        sec, usec = struct.unpack("<II", rec[:8])
        yield sec + usec / 1e6, rec[8], rec[9:]


def att_packets(path):
    """Reassembles fragmented ACL packets and yields (time, direction, conn, att_bytes)."""
    pending = {}  # (direction, conn) -> [time, l2cap_len, buffer]
    for ts, typ, p in read_pklg(path):
        if typ == 1 and len(p) >= 4 and p[0] == 0x05:  # Disconnection Complete
            yield ts, "DISC", struct.unpack("<H", p[3:5])[0], b""
            continue
        if typ not in (2, 3) or len(p) < 4:
            continue
        hdr, _ = struct.unpack("<HH", p[:4])
        conn, pb = hdr & 0x0FFF, (hdr >> 12) & 0x3
        direction = "TX" if typ == 2 else "RX"
        key = (direction, conn)
        data = p[4:]
        if pb == 0x1:  # continuation
            if key in pending:
                pending[key][2] += data
        else:
            if len(data) < 4:
                continue
            l2len, _ = struct.unpack("<HH", data[:4])
            pending[key] = [ts, l2len, bytearray(data)]
        if key in pending:
            t0, l2len, buf = pending[key]
            if len(buf) >= l2len + 4:
                del pending[key]
                cid = struct.unpack("<H", buf[2:4])[0]
                if cid == 0x0004:  # ATT
                    yield t0, direction, conn, bytes(buf[4 : 4 + l2len])


# ---------- Labels ----------
EFFECTS = {0x0F: "Twinkle", 0x13: "Fade", 0x14: "Breathe"}  # confirmed from capture


def rgb_name(c):
    if c == b"\x01\x01\x01":
        return "010101 (off)"
    return c.hex()


def ranges(nums):
    """[1,2,3,7,9,10] -> '1-3,7,9-10'"""
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(out)


def parse_diy(p):
    """a4 00 00 01 02 00 03 <effect> <speed> <brightness> <bg RGB> <groups u16> [groups...] <xor>"""
    if len(p) < 16:
        return f"DIY (short, {len(p)} B)"
    eff = p[7]
    info = (
        f"DIY | effect={eff:02x} ({EFFECTS.get(eff, '?')}) speed={p[8]}%"
        f" brightness={p[9]}% background={rgb_name(p[10:13])}"
    )
    n_groups = int.from_bytes(p[13:15], "little")
    pos, body = 15, p[:-1]
    for g in range(n_groups):
        try:
            count = int.from_bytes(body[pos : pos + 2], "little")
            rgb = body[pos + 2 : pos + 5]
            n_high = int.from_bytes(body[pos + 5 : pos + 7], "little")
            pos += 7
            low = list(body[pos : pos + count - n_high])
            pos += count - n_high
            high = [int.from_bytes(body[pos + 2 * k : pos + 2 * k + 2], "little") for k in range(n_high)]
            pos += 2 * n_high
            info += f"\n{'':41}Group {g + 1}: color {rgb_name(rgb)}, {count} LEDs: {ranges(sorted(low + high))}"
        except Exception:
            info += f"\n{'':41}Group {g + 1}: unreadable"
            break
    if n_groups == 0:
        info += " | no LEDs (settings only / drawing cleared)"
    if pos != len(body):
        info += f" | {len(body) - pos} unexpected trailing bytes"
    return info


def describe(p, direction):
    from_strip = direction == "RX"
    if p[:2] == b"\xe7\x01":
        return "Handshake 1" + (" (reply with session key)" if from_strip else "")
    if p[:2] == b"\xe7\x02":
        return "Handshake 2" + (" (reply)" if from_strip else "")
    if p[0] == 0xAA:
        return "Keepalive" + (" (reply)" if from_strip else "")
    if p[0] == 0xAB:
        if not from_strip:
            return f"Device info request {p[2]:02x}"
        return "Device info reply (contains identifiers, do not share)"
    if p[0] == 0xEE:
        return "Status from strip"
    if p[0] == 0xA4 and from_strip:
        return "DIY acknowledgement" if p[1] == 0x03 else f"DIY reply a4 {p[1]:02x}"
    if p[0] == 0x33 and from_strip:
        return f"Acknowledgement 33 {p[1]:02x}"
    if p[:2] == b"\x33\x01":
        state = {0x00: "OFF", 0x01: "ON"}.get(p[2], f"unknown value {p[2]:02x}")
        return f"Power {state}"
    if p[:2] == b"\x33\x04":
        return f"Brightness {p[2]}"
    if p[:4] == b"\x33\x05\x15\x01":
        return f"Whole-strip color: {p[4:7].hex()} (mask {p[12:14].hex()})"
    if p[:3] == b"\x33\x05\x0a":
        return "DIY apply"
    if p[:2] == b"\x33\x05":
        return f"Mode 33 05 {p[2]:02x} (unknown)"
    if p[:2] == b"\x33\x09":
        return "Init 33 09 (meaning unknown)"
    if p[0] == 0x33:
        return f"Command 33 {p[1]:02x} (unknown)"
    return "(unknown)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pklg")
    ap.add_argument("--no-keepalive", action="store_true")
    ap.add_argument("--only", nargs="*", help="only packets starting with these bytes (hex), e.g. a4 33")
    args = ap.parse_args()
    only = {int(x, 16) for x in args.only} if args.only else None

    write_h, notify_h = defaultdict(lambda: None), defaultdict(lambda: None)
    keys = {}  # conn -> session key
    handshake_sent = {}  # conn -> True once e7 01 came from the iPhone
    diy = {}  # conn -> drawing bytes of the parts sent so far

    for ts, direction, conn, att in att_packets(args.pklg):
        tm = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        if direction == "DISC":
            keys.pop(conn, None)
            handshake_sent.pop(conn, None)
            print(f"{tm}  ---- disconnected ----")
            continue
        op = att[0]
        # remember Govee characteristic handles from service discovery
        if op == 0x09 and direction == "RX":
            ln = att[1]
            for k in range(2, len(att) - ln + 1, ln):
                e = att[k : k + ln]
                if ln == 21:
                    vh = struct.unpack("<H", e[3:5])[0]
                    if e[5:7] == WRITE_SUFFIX:
                        write_h[conn] = vh
                    elif e[5:7] == NOTIFY_SUFFIX:
                        notify_h[conn] = vh
            continue
        if op == 0x52 or op == 0x12:  # Write Command / Request
            label = "iPhone → Strip"
        elif op == 0x1B:  # Notification
            label = "Strip → iPhone"
        else:
            continue
        handle = struct.unpack("<H", att[1:3])[0]
        if op == 0x12 and handle != write_h[conn] and len(att) <= 5:
            continue  # e.g. CCCD write
        raw = att[3:]
        if len(raw) < 4:
            continue

        # pick key: session key first, otherwise PSK (handshake)
        plain, used = None, None
        for name, k in (("S", keys.get(conn)), ("PSK", PSK)):
            if k is None:
                continue
            cand = decrypt(k, raw)
            if checksum_ok(cand):
                plain, used = cand, name
                break

        if plain is None:
            print(f"{tm}  {label}  ?? cannot decrypt (handshake missing?)  {raw.hex(' ')}")
            continue

        if plain[:2] == b"\xe7\x01" and used == "PSK":
            if op == 0x1B and handshake_sent.get(conn):
                keys[conn] = plain[2:18]
                print(f"{tm}  >>> Session key: {keys[conn].hex(' ')}")
            else:
                handshake_sent[conn] = True

        if args.no_keepalive and plain[0] == 0xAA:
            continue
        if only and plain[0] not in only:
            continue
        desc = describe(plain, direction)
        # a drawing is sent as parts a4 <part u16> <data> xor, the last one numbered ff ff
        if plain[0] == 0xA4 and direction != "RX":
            part = int.from_bytes(plain[1:3], "little")
            if part == 0xFFFF:
                desc = parse_diy(b"\xa4\x00\x00" + diy.pop(conn, b"") + plain[3:])
            else:
                diy[conn] = (diy.get(conn, b"") if part else b"") + plain[3:-1]
                desc = f"DIY part {part + 1} ({len(plain) - 4} bytes, decoded at the last part)"
        print(f"{tm}  {label}  {plain.hex(' ')}   {desc}")


if __name__ == "__main__":
    main()
