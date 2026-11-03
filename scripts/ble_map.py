#!/usr/bin/env python3
"""
Map LED positions: send the color codes of every LED to the H6871 over Bluetooth,
detect the LEDs live with OpenCV, decode each blob's LED ID and save its 2D position.

Before each batch an all-off reference image is taken; only light that differs
from it is detected. Each LED shows (n_digits + 1) Manchester pairs = 2 * (n_digits + 1) frames
(see led_id_encoding_decoding.py). Keep the camera still for the whole run.

Quit the Govee app first.

  python ble_map.py                     # all 375 LEDs
  python ble_map.py --leds 0-149        # subset
  python ble_map.py --selftest          # check grouping + decoding without hardware

Keys in the window: q = abort
"""

import argparse
import asyncio
import csv
import math
import time

import cv2
from bleak import BleakClient

from ble_track import APPLY, DIY_HEAD, END_MARKER, TOTAL_LEDS, WRITE_UUID, GoveeBLE, find_light, parse_leds, with_checksum
from capture import draw_leds, find_leds
from capture_manual import EXPOSURE, GAIN, apply_settings, open_camera
from led_id_encoding_decoding import decode, encode, n_digits_for

SETTLE = 0.8  # seconds to wait after a frame is sent before grabbing the camera image
MATCH_PX = 10  # detections closer than this in different frames are the same LED
OUTPUT = "led_positions.csv"


def frame_packet(groups: dict, brightness: int) -> bytes:
    """One DIY packet with several color groups, black background for unlisted LEDs."""
    # shortcut: multi-group layout guessed from the 1-group capture (<groups u16> then
    # per group: <count u16> RGB <high count u16> indices), confirm with a capture if colors mix up.
    body = bytearray(DIY_HEAD + bytes([brightness, 0, 0, 0]) + len(groups).to_bytes(2, "little"))
    for rgb, idx in groups.items():
        low = [i for i in idx if i < 256]
        high = [i for i in idx if i >= 256]
        body += len(idx).to_bytes(2, "little") + bytes(rgb) + len(high).to_bytes(2, "little") + bytes(low)
        for i in high:
            body += i.to_bytes(2, "little")
    return with_checksum(bytes(body))


def build_frames(leds: list[int], n_digits: int) -> list[dict]:
    """frames[k] = {(r, g, b): [led ids]} — slot A then slot B of each digit pair."""
    codes = {led: encode(led, n_digits) for led in leds}
    frames = []
    for d in range(n_digits + 1):
        for slot in (0, 1):
            groups = {}
            for led, pairs in codes.items():
                rgb = tuple(c * 255 for c in pairs[d][slot])
                groups.setdefault(rgb, []).append(led)
            frames.append(groups)
    return frames


def add_detections(tracks: list[dict], detections: list[dict], k: int, n_frames: int) -> None:
    for det in detections:
        best = min(tracks, key=lambda t: math.dist((t["x"], t["y"]), (det["x"], det["y"])), default=None)
        if best is None or math.dist((best["x"], best["y"]), (det["x"], det["y"])) > MATCH_PX or best["rgb"][k] is not None:
            best = {"x": det["x"], "y": det["y"], "rgb": [None] * n_frames}
            tracks.append(best)
        best["rgb"][k] = det["rgb"]


def decode_track(track: dict, n_digits: int) -> int | None:
    rgb = track["rgb"]
    if any(c is None for c in rgb):  # missed or clipped in some frame
        return None
    return decode(list(zip(rgb[0::2], rgb[1::2])), n_digits)


def batches(leds: list[int], n_digits: int, brightness: int, limit: int) -> list[list[int]]:
    """Split the LEDs into the fewest runs whose frames all fit in one BLE write."""
    for n in range(1, len(leds) + 1):
        size = math.ceil(len(leds) / n)
        chunks = [leds[i : i + size] for i in range(0, len(leds), size)]
        if all(len(frame_packet(f, brightness)) <= limit for c in chunks for f in build_frames(c, n_digits)):
            return chunks
    raise SystemExit(f"Even a single LED does not fit in {limit} bytes")


async def send_frame(g: GoveeBLE, groups: dict, brightness: int) -> None:
    async with g.lock:
        await g._send(frame_packet(groups, brightness))
        await g._send(END_MARKER)
        await g._send(APPLY)
    await asyncio.sleep(0.1)  # let the acks arrive before dropping them
    while not g.rx.empty():
        g.rx.get_nowait()


async def grab(cap, seconds: float, title: str, reference=None):
    """Show the live detection for `seconds`, return the last frame's detections and image.
    With a reference (all-off) image only light that changed is detected, not the lit room."""
    # runs on the main thread: macOS only allows cv2 windows there
    end, frame, leds = time.time() + seconds, None, []
    while time.time() < end or frame is None:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("Camera read failed")
        leds = find_leds(frame if reference is None else cv2.subtract(frame, reference))
        out = draw_leds(frame, leds)
        cv2.putText(out, title, (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.imshow("ble map", out)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            raise SystemExit("Aborted")
        await asyncio.sleep(0)  # let BLE keepalive / notifications run
    return leds, frame


async def run(args) -> None:
    leds = parse_leds(args.leds)
    n_digits = n_digits_for(TOTAL_LEDS)

    cap = open_camera()
    apply_settings(cap, EXPOSURE, GAIN)
    dev = await find_light(args.name)
    print(f"Connecting to {dev.name} ...")
    found = {}
    async with BleakClient(dev) as client:
        g = GoveeBLE(client)
        await g.handshake()
        ka = asyncio.create_task(g.keepalive())
        limit = client.services.get_characteristic(WRITE_UUID).max_write_without_response_size
        chunks = batches(leds, n_digits, args.brightness, limit)
        print(f"{len(chunks)} batch(es) of up to {len(chunks[0])} LEDs ({limit} bytes per write)")
        try:
            for b, chunk in enumerate(chunks):
                frames = build_frames(chunk, n_digits)
                tracks = []  # LEDs outside this batch are dark, so tracks start fresh
                await send_frame(g, {}, args.brightness)
                _, reference = await grab(cap, SETTLE, f"batch {b + 1}: reference (all off)")
                for k, groups in enumerate(frames):
                    await send_frame(g, groups, args.brightness)
                    title = f"batch {b + 1}/{len(chunks)} frame {k + 1}/{len(frames)}"
                    detections, _ = await grab(cap, SETTLE, title, reference)
                    add_detections(tracks, detections, k, len(frames))
                    print(f"{title}: {len(detections)} blobs")
                for t in tracks:
                    led = decode_track(t, n_digits)
                    if led in chunk and led not in found:  # duplicates: keep first, bad decodes give None
                        found[led] = (t["x"], t["y"])
                print(f"batch {b + 1}: decoded {sum(led in found for led in chunk)}/{len(chunk)}")
            await send_frame(g, {}, args.brightness)  # all off
        finally:
            ka.cancel()
            cap.release()
            cv2.destroyAllWindows()

    with open(OUTPUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["led", "x", "y"])
        for led in sorted(found):
            w.writerow([led, f"{found[led][0]:.1f}", f"{found[led][1]:.1f}"])
    print(f"Decoded {len(found)}/{len(leds)} LEDs -> {OUTPUT}")


def selftest() -> None:
    leds = list(range(TOTAL_LEDS))
    n_digits = n_digits_for(TOTAL_LEDS)
    frames = build_frames(leds, n_digits)
    # fake camera: LED i sits at (i * 30, 0) with the color it is sent in each frame
    tracks = []
    for k, groups in enumerate(frames):
        dets = [{"x": i * 30.0, "y": 0.0, "rgb": rgb} for rgb, idx in groups.items() for i in idx]
        add_detections(tracks, dets, k, len(frames))
    assert len(tracks) == TOTAL_LEDS
    assert all(decode_track(t, n_digits) == round(t["x"] / 30) for t in tracks)
    pkt = frame_packet(frames[0], 100)
    chunks = batches(leds, n_digits, 100, 509)
    assert len(chunks) == 2 and sum(map(len, chunks)) == TOTAL_LEDS
    print(f"selftest ok, {len(frames)} frames, packet {len(pkt)} bytes, {len(chunks)} batches at 509")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="6871", help="part of the light's Bluetooth name")
    ap.add_argument("--leds", action="append", default=[], help="index or range, default all")
    ap.add_argument("--brightness", type=int, default=30, help="1-100 %%, lower = less clipping")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    args.leds = args.leds or [f"0-{TOTAL_LEDS - 1}"]
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
