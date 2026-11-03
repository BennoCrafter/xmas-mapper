#!/usr/bin/env python3
"""
Per-LED control of a Govee H6871 over Bluetooth, from a Mac (or any PC with BLE).

Protocol decoded from a PacketLogger capture of the Govee Home app:
  * Govee "v1" encryption: handshake e7 01 / e7 02 with the public pre-shared key
    "MakingLifeSmarte"; the light's reply to e7 01 carries the session key
    (bytes 2-17). Every packet: AES-128-ECB on each full 16-byte block, RC4 on the
    remaining tail bytes (RC4 restarted per packet), same key for both.
  * Plaintext packets end with an XOR checksum of all preceding bytes.
  * Paint LEDs (one color per command):
      a4 00 00 | 01 02 00 03 14 33 64 ff ff ff | 01 00 |
      <count u16 LE> <R G B> <count of indices >= 256, u16 LE>
      <indices < 256, 1 byte each> <indices >= 256, u16 LE each> | xor
    then  a4 ff ff xor   (end marker, 4 bytes, not padded)
    then  33 05 0a 20 03 03 ... (20 bytes, apply/show)

Quit the Govee app first: only one Bluetooth client can be connected at a time.

  pip install bleak cryptography
  python govee_ble_leds.py --scan
  python govee_ble_leds.py --led 217 --color ff0000
  python govee_ble_leds.py --led 0-9 --led 100 --led 370-374 --color 00ff00
  python govee_ble_leds.py --brightness 30 --background 000000
  python govee_ble_leds.py          # stay connected, type "<leds> [color] [brightness]" lines, e.g. "0-9,100 00ff00 30"
                                    # or "bri 30", "bg 000000"
"""

import argparse
import asyncio
import os

from bleak import BleakClient, BleakScanner
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

WRITE_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
NOTIFY_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"
PSK = b"MakingLifeSmarte"
TOTAL_LEDS = 375
# DIY header: a4 00 00 01 02 00 03 <speed> <effect> <brightness %> <background RGB>
# <color groups u16 LE>. The app's brightness / speed / background / effect controls
# each send this header with 0 groups (16 bytes) + end + apply.
# shortcut: field meanings inferred from packet sizes (those captures had no handshake,
# so they could not be decrypted), confirm with a capture that includes the connect.
DIY_HEAD = bytes.fromhex("a40000 01020003 1433")  # 14 33 = speed, effect as the app sent them
# what the app sent: brightness 100, white background
DEFAULTS = {"brightness": 100, "background": b"\xff\xff\xff"}


# ---------- crypto ----------
def _rc4(key: bytes, data: bytes) -> bytes:
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


def encrypt(key: bytes, data: bytes) -> bytes:
    n = len(data) // 16 * 16
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data[:n]) + _rc4(key, data[n:])


def decrypt(key: bytes, data: bytes) -> bytes:
    n = len(data) // 16 * 16
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(data[:n]) + _rc4(key, data[n:])


def with_checksum(body: bytes) -> bytes:
    chk = 0
    for b in body:
        chk ^= b
    return body + bytes([chk])


def frame20(*data: int) -> bytes:
    return with_checksum(bytes(data).ljust(19, b"\x00"))


# ---------- packet builders ----------
def paint_packet(indices, rgb, brightness=100, background=b"\xff\xff\xff") -> bytes:
    idx = sorted(set(indices))
    low = [i for i in idx if i < 256]
    high = [i for i in idx if i >= 256]
    body = bytearray(DIY_HEAD + bytes([brightness]) + bytes(background))
    if not idx:  # settings only
        return with_checksum(bytes(body) + b"\x00\x00")
    body += b"\x01\x00" + len(idx).to_bytes(2, "little") + bytes(rgb) + len(high).to_bytes(2, "little")
    body += bytes(low)
    for i in high:
        body += i.to_bytes(2, "little")
    return with_checksum(bytes(body))


END_MARKER = with_checksum(bytes([0xA4, 0xFF, 0xFF]))
APPLY = frame20(0x33, 0x05, 0x0A, 0x20, 0x03, 0x03)
KEEPALIVE = frame20(0xAA, 0x00)


# ---------- BLE session ----------
class GoveeBLE:
    def __init__(self, client: BleakClient, verbose=False):
        self.c = client
        self.key = PSK
        self.rx = asyncio.Queue()
        self.verbose = verbose
        self.lock = asyncio.Lock()  # keepalive must not land between paint's 3 packets

    def _on_notify(self, _, data: bytearray):
        self.rx.put_nowait(bytes(data))

    async def _send(self, plain: bytes, key=None):
        if self.verbose:
            print("TX", plain.hex(" "))
        await self.c.write_gatt_char(WRITE_UUID, encrypt(key or self.key, plain), response=False)

    async def _recv(self, key=None, timeout=3.0) -> bytes:
        p = decrypt(key or self.key, await asyncio.wait_for(self.rx.get(), timeout))
        if self.verbose:
            print("RX", p.hex(" "))
        return p

    async def handshake(self):
        await self.c.start_notify(NOTIFY_UUID, self._on_notify)
        await self._send(with_checksum(b"\xe7\x01" + os.urandom(17)), PSK)
        reply = await self._recv(PSK)
        if reply[:2] != b"\xe7\x01":
            raise RuntimeError(f"Unexpected handshake reply: {reply.hex(' ')}")
        session_key = reply[2:18]
        await self._send(with_checksum(b"\xe7\x02" + os.urandom(17)), PSK)
        await self._recv(PSK)
        self.key = session_key

    async def keepalive(self):
        while True:
            await asyncio.sleep(2.5)
            async with self.lock:
                await self._send(KEEPALIVE)

    async def paint(self, indices, rgb, **settings):
        pkt = paint_packet(indices, rgb, **settings)
        limit = self.c.services.get_characteristic(WRITE_UUID).max_write_without_response_size
        if len(pkt) > limit:
            raise ValueError(
                f"{len(indices)} LEDs need {len(pkt)} bytes; this link allows {limit}. Paint fewer at once."
            )
        async with self.lock:
            await self._send(pkt)
            await self._send(END_MARKER)
            await self._send(APPLY)
            await asyncio.sleep(0.3)  # let the acks (a4 03 / 33 05) arrive
            while not self.rx.empty():
                await self._recv()


# ---------- CLI ----------
def parse_leds(specs):
    out = set()
    for spec in specs:
        for part in spec.split(","):
            a, _, b = part.partition("-")
            out.update(range(int(a), int(b or a) + 1))
    bad = [i for i in out if not 0 <= i < TOTAL_LEDS]
    if bad:
        raise SystemExit(f"LED numbers must be 0..{TOTAL_LEDS - 1}: {sorted(bad)}")
    return sorted(out)


def check_brightness(b):
    if not 1 <= b <= 100:
        raise ValueError(f"brightness must be 1..100, got {b}")
    return b


def parse_rgb(v):
    rgb = bytes.fromhex(v.lstrip("#"))
    if len(rgb) != 3:
        raise ValueError(f"color must be RRGGBB, got {v}")
    return rgb


# interactive "<word> <value>" commands that only change a header setting
SETTERS = {
    "bri": ("brightness", lambda v: check_brightness(int(v))),
    "bg": ("background", parse_rgb),
}


async def find_light(name_part):
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: name_part.lower() in (ad.local_name or d.name or "").lower(), timeout=10
    )
    if not dev:
        raise SystemExit(f"No Bluetooth device with '{name_part}' in its name. Is the Govee app closed?")
    return dev


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="list nearby BLE devices")
    ap.add_argument("--name", default="6871", help="part of the light's Bluetooth name")
    ap.add_argument("--led", action="append", default=[], help="index or range, e.g. 217 or 10-20")
    ap.add_argument("--color", default="ff0000", help="hex RGB")
    ap.add_argument("--brightness", type=int, help="1-100 %%")
    ap.add_argument("--background", type=parse_rgb, help="hex RGB (experimental)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print decrypted traffic")
    args = ap.parse_args()

    if args.scan:
        for d, ad in (await BleakScanner.discover(timeout=8, return_adv=True)).values():
            if ad.local_name:
                print(f"{ad.local_name:30s} {d.address}  rssi={ad.rssi}")
        return
    rgb = parse_rgb(args.color)
    if args.brightness is not None:
        check_brightness(args.brightness)
    given = {k: getattr(args, k) for k in DEFAULTS if getattr(args, k) is not None}
    st = DEFAULTS | given
    dev = await find_light(args.name)
    print(f"Connecting to {dev.name} ...")
    async with BleakClient(dev) as client:
        g = GoveeBLE(client, args.verbose)
        await g.handshake()
        ka = asyncio.create_task(g.keepalive())
        if args.led:
            leds = parse_leds(args.led)
            await g.paint(leds, rgb, **st)
            print(f"Painted {len(leds)} LED(s) #{rgb.hex()} @ {st['brightness']}%")
        elif given:
            await g.paint([], rgb, **st)
            print(f"Set {given}")
        else:
            print('Connected. Enter "<leds> [color] [brightness]" (e.g. "0-9,100 00ff00 30")')
            print('or "bri 30", "bg 000000"; empty line to quit.')
            while line := (await asyncio.to_thread(input, "> ")).strip():
                spec, *rest = line.split()
                try:
                    if spec in SETTERS:
                        key, parse = SETTERS[spec]
                        st[key] = parse(rest[0])
                        await g.paint([], rgb, **st)
                        print(f"{key} = {rest[0]}")
                        continue
                    if rest:
                        rgb = parse_rgb(rest[0])
                    if len(rest) > 1:
                        st["brightness"] = check_brightness(int(rest[1]))
                    leds = parse_leds([spec])
                    await g.paint(leds, rgb, **st)
                    print(f"Painted {len(leds)} LED(s) #{rgb.hex()} @ {st['brightness']}%")
                except (ValueError, IndexError, SystemExit) as e:
                    print(f"Error: {e}")
        ka.cancel()


if __name__ == "__main__":
    asyncio.run(main())
