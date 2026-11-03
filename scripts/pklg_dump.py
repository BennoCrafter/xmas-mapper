#!/usr/bin/env python3
"""
Decrypt and print the Govee BLE traffic in a PacketLogger (.pklg) capture.

  python scripts/pklg_dump.py capture.pklg            # hides the keepalives
  python scripts/pklg_dump.py capture.pklg --all

The capture has to include the connect (e7 01 / e7 02 handshake), because the
session key travels in it. Start PacketLogger first, then open the Govee app.
"""

import argparse
import struct

from ble_track import PSK, decrypt

NAMES = {
    "e7 01": "handshake 1 (key)",
    "e7 02": "handshake 2",
    "aa 00": "keepalive",
    "a4 00": "paint LEDs",
    "a4 ff": "paint end",
    "a4 03": "paint ack",
    "33 05": "apply / mode",
    "33 04": "brightness?",
    "33 01": "power?",
    "aa a5": "segment state (bri R G B)",
}


def records(path):
    data = open(path, "rb").read()
    # Mac captures are big-endian, iPhone live captures little-endian
    end = ">" if struct.unpack(">I", data[:4])[0] < 0x10000 else "<"
    pos = 0
    while pos + 4 <= len(data):
        ln = struct.unpack(end + "I", data[pos : pos + 4])[0]
        rec = data[pos + 4 : pos + 4 + ln]
        pos += 4 + ln
        if len(rec) > 8:
            secs, usecs = struct.unpack(end + "II", rec[:8])
            yield secs + usecs / 1e6, rec[8], rec[9:]


def att_values(path):
    """Yield (time, 'TX'|'RX', value) for ATT writes and notifications."""
    # shortcut: ignores ACL continuation fragments, fine while packets fit one ACL frame.
    for t, typ, p in records(path):
        if typ not in (2, 3) or len(p) < 12 or struct.unpack("<H", p[6:8])[0] != 4:
            continue  # not ACL, or not the ATT channel
        if p[8] in (0x52, 0x12, 0x1B):  # write cmd, write req, notification
            yield t, "TX" if typ == 2 else "RX", p[11:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pklg")
    ap.add_argument("--all", action="store_true", help="also show keepalives")
    args = ap.parse_args()

    key, new_key, t0 = PSK, None, None
    for t, d, val in att_values(args.pklg):
        if len(val) < 4:
            continue
        t0 = t0 or t
        # a (re)connect handshake is always under the pre-shared key
        plain = decrypt(PSK, val)
        if plain[:2] in (b"\xe7\x01", b"\xe7\x02"):
            if d == "RX" and plain[1] == 1:
                new_key = plain[2:18]
            elif d == "RX" and new_key:
                key, new_key = new_key, None
        else:
            plain = decrypt(key, val)
        cmd = plain[:2].hex(" ")
        if cmd == "aa 00" and not args.all:
            continue
        print(f"{t - t0:8.3f} {d} {NAMES.get(cmd, ''):26s} {plain.hex(' ')}")


if __name__ == "__main__":
    main()
