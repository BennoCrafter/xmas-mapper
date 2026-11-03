#!/usr/bin/env python3
"""
Per-LED control for Govee lights (e.g. H6871 Christmas Sparkle String Lights)
over the local LAN API, using the undocumented "razer" command that Govee
Desktop / Razer Chroma use for direct pixel streaming.

Requirements:
  * "LAN Control" switched on for the device in the Govee Home app
  * Your computer on the same network/subnet as the lights
  * UDP ports 4001-4003 free on this machine (only one LAN-API client per host)

Usage:
  python govee_pixels.py --discover
  python govee_pixels.py --ip 192.168.1.50 --demo chase
"""

import argparse
import base64
import colorsys
import json
import socket
import time

SCAN_ADDR = ("239.255.255.250", 4001)
LISTEN_PORT = 4002
CONTROL_PORT = 4003
MAX_COLORS = 100


# ---------- discovery ----------
def discover(timeout=3.0):
    """Multicast a scan and return the devices that answer."""
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("", LISTEN_PORT))
    rx.settimeout(0.5)

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    msg = {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}
    for _ in range(3):  # UDP multicast over Wi-Fi drops packets; send a few
        tx.sendto(json.dumps(msg).encode(), SCAN_ADDR)

    found, end = [], time.time() + timeout
    while time.time() < end:
        try:
            data, _ = rx.recvfrom(4096)
            dev = json.loads(data)["msg"]["data"]
            if dev not in found:
                found.append(dev)
        except TimeoutError:
            pass
    rx.close()
    tx.close()
    return found


# ---------- razer / DreamView packet builder ----------
def _packet(cmd: int, payload: bytes) -> bytes:
    """0xBB, size_hi, size_lo, cmd, payload..., xor_checksum"""
    size = len(payload)
    body = bytes([0xBB, (size >> 8) & 0xFF, size & 0xFF, cmd]) + payload
    chk = 0
    for b in body:
        chk ^= b
    return body + bytes([chk])


class GoveePixels:
    def __init__(self, ip: str, num_leds: int):
        # H6871 silently drops frames with more colors (100 works, 120 doesn't). The 100
        # slots spread evenly over all 375 LEDs (slot i ~ LED i*3.75, lights 1-2 LEDs each).
        if num_leds > MAX_COLORS:
            raise ValueError(f"H6871 ignores frames with more than {MAX_COLORS} colors.")
        self.addr = (ip, CONTROL_PORT)
        self.n = num_leds
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._last_enable = 0.0

    def _send_raw(self, pkt: bytes):
        msg = {"msg": {"cmd": "razer", "data": {"pt": base64.b64encode(pkt).decode()}}}
        self.sock.sendto(json.dumps(msg).encode(), self.addr)

    def _send_json(self, cmd, data):
        self.sock.sendto(json.dumps({"msg": {"cmd": cmd, "data": data}}).encode(), self.addr)

    def turn_on(self):
        self._send_json("turn", {"value": 1})

    def brightness(self, pct: int):
        self._send_json("brightness", {"value": max(1, min(100, pct))})

    def enable(self, on=True):
        # Expected: BB 00 01 B1 01 0A  (base64 "uwABsQEK")
        self._send_raw(_packet(0xB1, bytes([1 if on else 0])))
        self._last_enable = time.time()

    def show(self, colors, gradient=False):
        """colors: list of (r, g, b) tuples, one per LED (index 0 = start of string)."""
        if time.time() - self._last_enable > 20:  # device drops out after ~60 s idle
            self.enable(True)
        colors = list(colors)[: self.n]
        payload = bytearray([1 if gradient else 0, len(colors)])
        for r, g, b in colors:
            payload += bytes((int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF))
        self._send_raw(_packet(0xB0, bytes(payload)))

    def close(self):
        self.enable(False)  # hand control back to the app's scene
        self.sock.close()

    def send_ble(self, *packets: bytes):
        """Send raw Govee BLE commands over LAN via the 'ptReal' passthrough."""
        encoded = []
        for p in packets:
            p = bytes(p[:19]).ljust(19, b"\x00")
            chk = 0
            for b in p:
                chk ^= b
            encoded.append(base64.b64encode(p + bytes([chk])).decode())
        self._send_json("ptReal", {"command": encoded})


# ---------- demos ----------
def demo_single(px, index):
    print(px.n)
    frame = [(0, 0, 0)] * px.n
    frame[index] = (255, 255, 255)
    px.show(frame)
    print(f"LED {index} is white; Ctrl+C to stop")
    while True:
        time.sleep(1)
        px.show(frame)


def demo_chase(px, fps):
    i = 0
    while True:
        frame = [(10, 0, 0)] * px.n
        for t in range(5):  # little tail
            frame[(i - t) % px.n] = (255 // (t + 1), 180 // (t + 1), 40 // (t + 1))
        px.show(frame)
        i = (i + 1) % px.n
        time.sleep(1 / fps)


def demo_rainbow(px, fps):
    offset = 0.0
    while True:
        frame = []
        for k in range(px.n):
            r, g, b = colorsys.hsv_to_rgb((k / px.n + offset) % 1.0, 1, 1)
            frame.append((r * 255, g * 255, b * 255))
        px.show(frame)
        offset += 0.01
        time.sleep(1 / fps)


def demo_twinkle(px, fps):
    import random

    frame = [(0, 40, 0)] * px.n
    while True:
        for _ in range(max(1, px.n // 25)):
            frame[random.randrange(px.n)] = random.choice([(255, 0, 0), (255, 200, 80), (0, 255, 0), (255, 255, 255)])
        frame = [(max(0, r - 15), max(40 if g >= 40 else 0, g - 15), max(0, b - 15)) for r, g, b in frame]
        px.show(frame)
        time.sleep(1 / fps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--ip")
    ap.add_argument("--leds", type=int, default=MAX_COLORS)
    ap.add_argument("--demo", choices=["single", "chase", "rainbow", "twinkle"], default="chase")
    ap.add_argument("--index", type=int, default=0, help="LED for --demo single")
    ap.add_argument("--fps", type=float, default=25)
    ap.add_argument("--raw")
    args = ap.parse_args()

    if args.discover or not args.ip:
        found = discover()
        for d in found:
            print(f"{d.get('sku')}  ip={d.get('ip')}  device={d.get('device')}")
        if not found:
            print(
                "No Govee devices answered. Check: LAN Control enabled in the Govee Home app, "
                "lights on the same Wi-Fi/subnet (not a guest network), and macOS "
                "System Settings > Privacy & Security > Local Network allows your terminal."
            )
        return

    px = GoveePixels(args.ip, args.leds)
    px.turn_on()
    px.brightness(100)
    time.sleep(0.2)
    px.enable(True)
    time.sleep(0.2)

    px.send_ble(bytes([0x33, 0x01, 0x00]))

    try:
        if args.demo == "single":
            demo_single(px, args.index)
        elif args.demo == "chase":
            demo_chase(px, args.fps)
        elif args.demo == "rainbow":
            demo_rainbow(px, args.fps)
        else:
            demo_twinkle(px, args.fps)
    except KeyboardInterrupt:
        pass
    finally:
        px.close()


if __name__ == "__main__":
    main()
