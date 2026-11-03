#!/usr/bin/env python3
"""
Light one LED of a Govee H6871 (375 LEDs) in a chosen color.
Put this file next to govee_pixels.py.

  razer mode (works now): lights the nearest of the 100 streaming slots,
                          so it is accurate to about +-2 LEDs.
      python set_led.py --ip 192.168.1.50 --led 217 --color ff0000

  probe mode (experimental): sends Govee's BLE "segment color" command over
                          LAN via ptReal. Known from other Govee RGBIC models,
                          NOT confirmed on the H6871. Watch what lights up.
      python set_led.py --ip 192.168.1.50 --led 0 --color ff0000 --probe
"""

import argparse
import base64
import time

from control_h6871 import MAX_COLORS, GoveePixels

TOTAL_LEDS = 375


def ble_packet(*data: int) -> str:
    """19 data bytes (zero-padded) + XOR checksum, Base64 for ptReal."""
    p = bytes(data).ljust(19, b"\x00")
    chk = 0
    for b in p:
        chk ^= b
    return base64.b64encode(p + bytes([chk])).decode()


def razer_single(px: GoveePixels, led: int, rgb):
    slot = min(MAX_COLORS - 1, round(led * MAX_COLORS / TOTAL_LEDS))
    print(f"LED {led} -> slot {slot} (lands near LED {slot * TOTAL_LEDS / MAX_COLORS:.0f})")
    frame = [(0, 0, 0)] * MAX_COLORS
    frame[slot] = rgb
    px.enable(True)
    time.sleep(0.2)
    print("Holding; Ctrl+C to stop")
    try:
        while True:  # resend so the device doesn't drop out of streaming mode
            px.show(frame)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        px.close()


def probe_segment(px: GoveePixels, seg: int, rgb):
    if seg > 55:
        raise ValueError("This command format only has room for segments 0-55.")
    px.enable(False)  # leave streaming mode so normal commands are accepted
    time.sleep(0.2)
    # 33 05 15 01 RR GG BB 00 00 00 00 00 <segment bitmask, little-endian>
    mask = (1 << seg).to_bytes(7, "little")
    pkt = ble_packet(0x33, 0x05, 0x15, 0x01, *rgb, 0, 0, 0, 0, 0, *mask)
    px._send_json("ptReal", {"command": [pkt]})
    print(f"Sent segment-color probe for segment {seg}: {pkt}")
    print("If nothing changed, the H6871 uses a different command: capture it from the app.")
    px.sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True)
    ap.add_argument("--led", type=int, required=True, help=f"0..{TOTAL_LEDS - 1}")
    ap.add_argument("--color", default="ff0000", help="hex RGB, e.g. ff0000")
    ap.add_argument("--probe", action="store_true", help="try the ptReal segment command")
    args = ap.parse_args()

    if not 0 <= args.led < TOTAL_LEDS:
        ap.error(f"--led must be 0..{TOTAL_LEDS - 1}")
    rgb = tuple(bytes.fromhex(args.color.lstrip("#")))

    px = GoveePixels(args.ip, MAX_COLORS)
    px.turn_on()
    px.brightness(100)
    time.sleep(0.2)

    if args.probe:
        probe_segment(px, args.led, rgb)
    else:
        razer_single(px, args.led, rgb)


if __name__ == "__main__":
    main()
