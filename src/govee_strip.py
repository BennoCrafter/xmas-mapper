"""
Per-LED control of a Govee H6871 over Bluetooth, from a Mac (or any PC with BLE).

Protocol (decoded from PacketLogger captures of the Govee Home app):
  * Handshake e7 01 / e7 02, encrypted with the public key "MakingLifeSmarte".
    The light's reply to e7 01 carries the session key (bytes 2-17).
  * Every packet: AES-128-ECB on each full 16-byte block, RC4 (restarted per packet)
    on the remaining tail bytes, same key for both.
  * Plaintext packets end with an XOR checksum of all preceding bytes.
  * DIY drawing (confirmed):
      a4 00 00 01 <packets> 00 03 <effect> <speed %> <brightness %> <background RGB>
      <groups u16 LE> then per group:
        <count u16 LE> <R G B> <count of indices >= 256, u16 LE>
        <indices < 256, 1 byte each> <indices >= 256, u16 LE each>
      <xor>
    split into parts of at most 505 bytes (the app's size): a4 <part u16 LE> <data> <xor>,
    the last part numbered ff ff, so a small drawing is  a4 00 00 <all> xor  +  a4 ff ff xor
    (light answers a4 03), middle parts numbered 01 00, 02 00, ... <packets> counts all parts,
    the empty last one included. Confirmed up to 3 parts (124 colors), see demo3.pklg.
    then  33 05 0a 20 03 03 ...    (switch to DIY mode, light answers 33 05 00)
  * Whole-strip color (confirmed): 33 05 15 01 RR GG BB 00 00 00 00 00 ff 7f ...
  * Background "off" as sent by the app: 01 01 01.
  * Power on/off 33 01 01 / 33 01 00: common Govee format
  * No "static / no effect" code has been captured yet. The default below
    (Fade at speed 0) is an assumption; see STATIC_EFFECT.

Quit the Govee app first: only one Bluetooth client can be connected at a time.

  pip install bleak cryptography
  python main.py leds --scan
  python main.py leds --on 217 --color ff0000
  python main.py leds --on 0-9,100 --color 00ff00 --on 370-374 --color 0000ff
  python main.py leds --fill ff8000
  python main.py leds --power off
  python main.py leds              # interactive, type "help"

Use as a library:

  async with GoveeStrip("6871") as strip:
      strip.set("0-9", "ff0000")
      strip.set([20, 21, 22], (0, 255, 0))
      strip.off("5")
      await strip.show()
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

OFF = b"\x01\x01\x01"  # what the app sends for "background off"

# effect codes (byte 7 of the DIY header), confirmed from captures
EFFECTS = {"twinkle": 0x0F, "fade": 0x13, "breathe": 0x14}
# assumption: an effect at speed 0 should stand still. Change once a real
# "static" code has been captured.
STATIC_EFFECT = ("fade", 0)


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


def xor_all(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


def with_checksum(body: bytes) -> bytes:
    return body + bytes([xor_all(body)])


def checksum_ok(p: bytes) -> bool:
    return len(p) > 1 and xor_all(p[:-1]) == p[-1]


def frame20(*data: int) -> bytes:
    return with_checksum(bytes(data).ljust(19, b"\x00"))


# ---------- value parsing ----------
def parse_rgb(v) -> bytes:
    """'ff0000', '#ff0000', 'off', (255, 0, 0) or b'\\xff\\x00\\x00' -> 3 bytes."""
    if isinstance(v, (bytes, bytearray)) or isinstance(v, (tuple, list)):
        rgb = bytes(v)
    elif str(v).lower() == "off":
        return OFF
    else:
        rgb = bytes.fromhex(str(v).lstrip("#"))
    if len(rgb) != 3:
        raise ValueError(f"color must be RRGGBB, got {v!r}")
    if rgb == b"\x00\x00\x00":
        rgb = OFF  # the app never sends 000000; use its "off" value instead
    return rgb


def parse_leds(spec) -> list:
    """'0-9,100' or 217 or [1, 2, 3] -> sorted list of LED indices."""
    if isinstance(spec, int):
        out = {spec}
    elif isinstance(spec, str):
        out = set()
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            a, _, b = part.partition("-")
            out.update(range(int(a), int(b or a) + 1))
    else:
        out = set(spec)
    bad = [i for i in out if not 0 <= i < TOTAL_LEDS]
    if bad:
        raise ValueError(f"LED numbers must be 0..{TOTAL_LEDS - 1}: {sorted(bad)}")
    return sorted(out)


def parse_effect(v) -> int:
    if isinstance(v, int):
        return v
    v = str(v).lower()
    if v in EFFECTS:
        return EFFECTS[v]
    return int(v, 16)


def check_percent(name, v) -> int:
    v = int(v)
    if not 0 <= v <= 100:
        raise ValueError(f"{name} must be 0..100, got {v}")
    return v


# ---------- packet builders ----------
PART_SIZE = 505  # the app splits drawings into packets of this size


def diy_packet(groups, effect, speed, brightness, background) -> bytes:
    """groups: list of (rgb bytes, [indices])"""
    body = bytearray(b"\xa4\x00\x00\x01\x02\x00\x03")
    body += bytes([effect, speed, brightness]) + background
    body += len(groups).to_bytes(2, "little")
    for rgb, idx in groups:
        low = [i for i in idx if i < 256]
        high = [i for i in idx if i >= 256]
        body += len(idx).to_bytes(2, "little") + rgb + len(high).to_bytes(2, "little")
        body += bytes(low)
        for i in high:
            body += i.to_bytes(2, "little")
    return with_checksum(bytes(body))


def diy_parts(pkt: bytes, size=PART_SIZE) -> list:
    """Split a drawing from diy_packet() into the packets the light expects."""
    data, n = bytearray(pkt[3:-1]), size - 4
    count = max(2, -(-len(data) // n))  # the last part is always sent, even empty
    data[1] = count  # the light only draws as many parts as the header announces
    chunks = [data[i : i + n] for i in range(0, len(data), n)]
    if len(chunks) < 2:
        chunks.append(b"")
    return [
        with_checksum(b"\xa4" + (i if i < len(chunks) - 1 else 0xFFFF).to_bytes(2, "little") + bytes(c))
        for i, c in enumerate(chunks)
    ]


def strip_color_packet(rgb: bytes) -> bytes:
    return frame20(0x33, 0x05, 0x15, 0x01, *rgb, 0, 0, 0, 0, 0, 0xFF, 0x7F)


DIY_MODE = frame20(0x33, 0x05, 0x0A, 0x20, 0x03, 0x03)
KEEPALIVE = frame20(0xAA, 0x00)


# ---------- the controller ----------
class GoveeStrip:
    """
    Keeps the state of every LED on the computer and sends it as one DIY drawing.

    set()/off()/clear() only change the local state; show() sends it.
    LEDs that were never set (or were turned off) show the background color,
    which is "off" by default, so only lit LEDs need to be sent.
    """

    def __init__(self, name="6871", address=None, verbose=False):
        self.name = name
        self.address = address
        self.verbose = verbose
        self.leds = {}  # index -> rgb bytes
        effect, speed = STATIC_EFFECT
        self.effect = EFFECTS[effect]
        self.speed = speed
        self.brightness = 100
        self.background = OFF
        self._client = None
        self._key = PSK
        self._rx = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._ka_task = None
        self._pending = 0  # drawings sent whose replies were not read yet

    # ----- connection -----
    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    async def connect(self):
        if self.address:
            target = self.address
        else:
            target = await BleakScanner.find_device_by_filter(
                lambda d, ad: self.name.lower() in (ad.local_name or d.name or "").lower(),
                timeout=10,
            )
            if not target:
                raise ConnectionError(f"No Bluetooth device with '{self.name}' in its name. Is the Govee app closed?")
        self._client = BleakClient(target)
        await self._client.connect()
        await self._client.start_notify(NOTIFY_UUID, self._on_notify)
        await self._handshake()
        self._ka_task = asyncio.create_task(self._keepalive())

    async def disconnect(self):
        if self._ka_task:
            self._ka_task.cancel()
            self._ka_task = None
        if self._client and self._client.is_connected:
            await self._wait_acks()  # don't cut off the last show(wait=False)
        if self._client and self._client.is_connected:
            await self._client.disconnect()

    @property
    def max_packet(self) -> int:
        char = self._client.services.get_characteristic(WRITE_UUID)
        return char.max_write_without_response_size

    # ----- local state -----
    def set(self, leds: list[int], color):
        """Set the color of the specified LEDs. Run show() to send the changes."""
        rgb = parse_rgb(color)
        for i in parse_leds(leds):
            self.leds[i] = rgb
        return self

    def off(self, leds):
        for i in parse_leds(leds):
            self.leds.pop(i, None)
        return self

    def clear(self):
        self.leds.clear()
        return self

    def set_effect(self, effect, speed=None):
        self.effect = parse_effect(effect)
        if speed is not None:
            self.speed = check_percent("speed", speed)
        return self

    def set_static(self):
        effect, speed = STATIC_EFFECT
        return self.set_effect(effect, speed)

    def set_brightness(self, percent):
        self.brightness = check_percent("brightness", percent)
        if self.brightness == 0:
            raise ValueError("brightness 0 is not allowed, use power(False)")
        return self

    def set_background(self, color):
        self.background = parse_rgb(color)
        return self

    def _groups(self, background=None):
        """LEDs grouped by color, leaving out those the background already shows (unset LEDs are off)."""
        background = background or self.background
        by_color = {}
        for i in range(TOTAL_LEDS):
            rgb = self.leds.get(i, OFF)
            if rgb != background:
                by_color.setdefault(rgb, []).append(i)
        return list(by_color.items())

    def packet(self, limit=None) -> bytes:
        """The drawing. If it is longer than limit, the color giving the shortest packet becomes the
        background so fewer LEDs are sent; that is how the app fits (almost) the whole strip in one packet."""
        pkt = diy_packet(self._groups(), self.effect, self.speed, self.brightness, self.background)
        if limit and len(pkt) > limit:
            # not simply the most common color: LEDs >= 256 cost 2 bytes each
            colors = {self.leds.get(i, OFF) for i in range(TOTAL_LEDS)}
            pkt = min(
                (diy_packet(self._groups(c), self.effect, self.speed, self.brightness, c) for c in colors),
                key=len,
            )
        return pkt

    # ----- sending -----
    async def show(self, wait=True):
        """
        Send the current state as a DIY drawing.

        wait=False returns once the drawing is sent and only waits for the previous
        drawing's replies, so the next frame overlaps the light's answer (~2x faster
        for animations).
        """
        size = min(self.max_packet, PART_SIZE)
        parts = diy_parts(self.packet(size), size)
        async with self._lock:
            if not self._pending:
                self._drain()
            # all back to back, the light answers in order: one round trip, not two
            for part in parts:
                await self._send(part)
            await self._send(DIY_MODE)
            self._pending += 1
            await self._wait_acks(0 if wait else 1)

    async def fill(self, color):
        """Whole strip in one color (separate mode, ignores the DIY state)."""
        async with self._lock:
            await self._wait_acks()
            self._drain()
            await self._send(strip_color_packet(parse_rgb(color)))
            await self._expect(b"\x33\x05")

    async def _wait_acks(self, keep=0):
        """Read replies of sent drawings until only `keep` are left unanswered. Call with the lock held."""
        try:
            while self._pending > keep:
                await self._expect(b"\xa4\x03")
                await self._expect(b"\x33\x05")
                self._pending -= 1
        except TimeoutError:
            self._pending = 0
            raise

    async def power(self, on: bool = True):
        async with self._lock:
            await self._wait_acks()
            self._drain()
            await self._send(frame20(0x33, 0x01, 0x01 if on else 0x00))
            await self._expect(b"\x33\x01")

    # ----- low level -----
    def _on_notify(self, _, data: bytearray):
        self._rx.put_nowait(bytes(data))

    def _decode(self, raw, key=None):
        p = decrypt(key or self._key, raw)
        if not checksum_ok(p):
            p = decrypt(PSK, raw)  # e.g. a status packet before the handshake
            if not checksum_ok(p):
                return None
        if self.verbose:
            print("RX", p.hex(" "))
        return p

    async def _send(self, plain: bytes, key=None):
        if self.verbose:
            print("TX", plain.hex(" "))
        await self._client.write_gatt_char(WRITE_UUID, encrypt(key or self._key, plain), response=False)

    async def _expect(self, prefix: bytes, timeout=3.0, key=None) -> bytes:
        """Wait for a reply starting with prefix, skipping keepalive echoes and status."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            left = end - loop.time()
            if left <= 0:
                raise TimeoutError(f"no reply {prefix.hex(' ')} from the light")
            raw = await asyncio.wait_for(self._rx.get(), left)
            p = self._decode(raw, key)
            if p and p.startswith(prefix):
                return p

    def _drain(self):
        while not self._rx.empty():
            self._decode(self._rx.get_nowait())

    async def _handshake(self):
        self._key = PSK
        await self._send(with_checksum(b"\xe7\x01" + os.urandom(17)), PSK)
        reply = await self._expect(b"\xe7\x01", key=PSK)
        session_key = reply[2:18]
        await self._send(with_checksum(b"\xe7\x02" + os.urandom(17)), PSK)
        await self._expect(b"\xe7\x02", key=PSK)
        self._key = session_key

    async def _keepalive(self):
        while True:
            await asyncio.sleep(2.5)
            async with self._lock:
                await self._send(KEEPALIVE)
            await asyncio.sleep(0.2)
            async with self._lock:
                if not self._pending:
                    self._drain()


# ---------- CLI ----------
HELP = """commands:
  on <leds> [color]    light LEDs, e.g. "on 0-9,100 ff0000" (color: RRGGBB, default last color)
  off <leds>           turn LEDs off, e.g. "off 5"
  clear                all LEDs off
  fill <color>         whole strip one color (separate mode)
  bri <1-100>          brightness
  bg <color|off>       background color for all LEDs not set
  effect <name|hex> [speed]   twinkle, fade, breathe or a hex code
  static               no effect (assumed: fade at speed 0)
  speed <0-100>        effect speed
  power on|off         power
  list                 show which LEDs are set
  empty line           quit"""


async def interactive(strip: GoveeStrip):
    print('Connected. Type "help" for commands.')
    color = b"\xff\x00\x00"
    while line := (await asyncio.to_thread(input, "> ")).strip():
        cmd, *args = line.split()
        cmd = cmd.lower()
        try:
            if cmd == "help":
                print(HELP)
                continue
            if cmd == "list":
                groups = strip._groups()
                if not groups:
                    print("no LEDs set")
                for rgb, idx in groups:
                    print(f"{rgb.hex()}: {len(idx)} LEDs")
                continue
            if cmd == "fill":
                await strip.fill(args[0])
                continue
            if cmd == "power":
                await strip.power(args[0].lower() == "on")
                continue
            if cmd == "on":
                if len(args) > 1:
                    color = parse_rgb(args[1])
                strip.set(args[0], color)
            elif cmd == "off":
                strip.off(args[0])
            elif cmd == "clear":
                strip.clear()
            elif cmd == "bri":
                strip.set_brightness(args[0])
            elif cmd == "bg":
                strip.set_background(args[0])
            elif cmd == "effect":
                strip.set_effect(args[0], args[1] if len(args) > 1 else None)
            elif cmd == "static":
                strip.set_static()
            elif cmd == "speed":
                strip.set_effect(strip.effect, args[0])
            else:
                print('unknown command, type "help"')
                continue
            await strip.show()
        except (ValueError, IndexError, TimeoutError) as e:
            print(f"Error: {e}")


async def main():
    ap = argparse.ArgumentParser(description="Control a Govee H6871 over Bluetooth.")
    ap.add_argument("--scan", action="store_true", help="list nearby BLE devices")
    ap.add_argument("--name", default="6871", help="part of the light's Bluetooth name")
    ap.add_argument("--address", help="connect by address/UUID instead of name")
    ap.add_argument("--on", action="append", default=[], help="LEDs to light, e.g. 217 or 0-9,100")
    ap.add_argument(
        "--color", action="append", default=[], help="color for the matching --on (RRGGBB); the last one is reused"
    )
    ap.add_argument("--fill", help="whole strip one color")
    ap.add_argument("--power", choices=["on", "off"])
    ap.add_argument("--brightness", type=int, help="1-100")
    ap.add_argument("--background", help="RRGGBB or off (default off)")
    ap.add_argument("--effect", help="twinkle, fade, breathe or hex code (default: static)")
    ap.add_argument("--speed", type=int, help="0-100")
    ap.add_argument("-v", "--verbose", action="store_true", help="print decrypted traffic")
    args = ap.parse_args()

    if args.scan:
        for d, ad in (await BleakScanner.discover(timeout=8, return_adv=True)).values():
            if ad.local_name:
                print(f"{ad.local_name:30s} {d.address}  rssi={ad.rssi}")
        return

    async with GoveeStrip(args.name, args.address, args.verbose) as strip:
        print("Connected.")
        if args.power:
            await strip.power(args.power == "on")
        if args.fill:
            await strip.fill(args.fill)
        if args.brightness is not None:
            strip.set_brightness(args.brightness)
        if args.background:
            strip.set_background(args.background)
        if args.effect:
            strip.set_effect(args.effect)
        if args.speed is not None:
            strip.set_effect(strip.effect, args.speed)
        for n, leds in enumerate(args.on):
            colors = args.color or ["ff0000"]
            strip.set(leds, colors[min(n, len(colors) - 1)])

        drawing_args = (
            args.on or args.brightness is not None or args.background or args.effect or args.speed is not None
        )
        if drawing_args:
            await strip.show()
            print(f"Sent drawing: {len(strip.leds)} LED(s) lit")
        elif not (args.power or args.fill):
            await interactive(strip)


if __name__ == "__main__":
    asyncio.run(main())
