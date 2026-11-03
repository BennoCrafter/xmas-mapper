"""
Find LEDs in a camera stream, video file or image and measure each one's color.

An LED is a small spot brighter than its surroundings (top-hat filter), so the
glow it throws on walls and tables is ignored. Each LED is one local brightness
maximum, so LEDs on a strip whose glows touch still count separately: the glow
between two neighbors is split between them (nearest center wins).
Its position is the brightness-weighted center (sub-pixel), its color the mean
RGB of its unclipped pixels (overexposed centers are white and carry no color,
the glow around them does).

    python capture.py                       # camera, manual settings from capture_manual.py
    python capture.py video.mp4
    python capture.py photo.jpg             # one image: print the LEDs, show the result
    python capture.py photo.jpg --no-show --out result.png --mask-out mask.png
    python capture.py photo.jpg --scale 0.25 --spacing 25 --min-peak 215   # bright phone photo
    python capture.py --selftest            # check detection on synthetic images
    python capture.py --help                # all options

Keys: p = print LEDs, m = show detection mask (for tuning), space = pause, q = quit
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

THRESHOLD = 60  # how much brighter (0-255) than its surroundings a pixel must be to belong to an LED
LED_SIZE = 41  # px, larger than the biggest LED incl. its halo; anything wider counts as background glow
SPACING = 9  # px, a bit less than the distance between two neighboring LEDs in the image
BLUR = 5  # px, merges the speckles of one diffuse LED into one spot
MIN_AREA = 4  # px, smaller = noise
MIN_PEAK = 0  # 0-255, >0 = bright photo: LEDs are found by their white centers (all channels >= this), ~215
CLIP = 250  # all channels at or above this = clipped, no color information
COLOR_FRACTION = 0.25  # pixels at least this fraction of the LED's peak brightness give its color
ON_RATIO = 0.5  # a channel is "on" at half the brightest channel (for the name only)

NAMES = {
    (1, 0, 0): "red",
    (0, 1, 0): "green",
    (0, 0, 1): "blue",
    (1, 1, 0): "yellow",
    (1, 0, 1): "magenta",
    (0, 1, 1): "cyan",
    (1, 1, 1): "white",
}


def led_mask(frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """rgb, brightness, smoothed brightness, mask of pixels that belong to some LED."""
    rgb = frame_bgr[..., ::-1].astype(np.float32)  # OpenCV delivers BGR
    brightness = rgb.max(axis=2)
    if MIN_PEAK:  # bright photo: wood and paper are as bright as a glow, only LED centers blow out to white
        whiteness = cv2.GaussianBlur(rgb.min(axis=2), (BLUR, BLUR), 0)
        return rgb, brightness, whiteness, whiteness >= MIN_PEAK
    smooth = cv2.GaussianBlur(brightness, (BLUR, BLUR), 0)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (LED_SIZE, LED_SIZE))
    mask = cv2.morphologyEx(smooth, cv2.MORPH_TOPHAT, kernel) >= THRESHOLD
    return rgb, brightness, smooth, mask


def find_peaks(smooth: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """(x, y) of each LED center, at least SPACING / 2 apart."""
    near = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SPACING, SPACING))
    is_max = mask & (smooth >= cv2.dilate(smooth, near))
    # a clipped center is a flat plateau of equal maxima: one plateau = one candidate
    n, _, _, centers = cv2.connectedComponentsWithStats(is_max.astype(np.uint8), connectivity=8)
    centers = np.round(centers[1:]).astype(int)
    centers = centers[mask[centers[:, 1], centers[:, 0]]]  # a ring-shaped plateau's center can fall outside
    centers = centers[
        smooth[centers[:, 1], centers[:, 0]] >= MIN_PEAK
    ]  # LEDs are the brightest things; highlights are not
    # equal maxima close together (noise on a flat top) would count twice: keep the brighter one
    centers = centers[np.argsort(-smooth[centers[:, 1], centers[:, 0]], kind="stable")]
    kept = []
    for c in centers:
        if all((c[0] - k[0]) ** 2 + (c[1] - k[1]) ** 2 >= (SPACING / 2) ** 2 for k in kept):
            kept.append(c)
    return np.array(kept, int).reshape(-1, 2)


def find_leds(frame_bgr: np.ndarray) -> list[dict]:
    """[{x, y, area, rgb, name}], rgb is None when the whole LED is clipped."""
    rgb, brightness, smooth, mask = led_mask(frame_bgr)
    peaks = find_peaks(smooth, mask)
    if not len(peaks):
        return []
    px, py = peaks[:, 0], peaks[:, 1]

    # give every LED pixel to the nearest center within the same blob
    src = np.full(mask.shape, 255, np.uint8)
    src[py, px] = 0
    dist, nearest = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    led_of_label = np.full(nearest.max() + 1, -1)
    led_of_label[nearest[py, px]] = np.arange(len(peaks))
    owner = led_of_label[nearest]
    nearby = np.where(dist <= LED_SIZE / 2, owner, -1)
    _, blob = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    owner[~mask | (blob != blob[py, px][owner])] = -1

    # per-LED sums in one pass each: index i + 1 collects the values of LED i's pixels, 0 the rest
    n = len(peaks) + 1
    idx = (owner + 1).ravel()
    ys, xs = np.indices(mask.shape)
    w = brightness.ravel()
    area = np.bincount(idx, minlength=n)
    w_sum = np.maximum(np.bincount(idx, w, n), 1)
    cx = np.bincount(idx, xs.ravel() * w, n) / w_sum
    cy = np.bincount(idx, ys.ravel() * w, n) / w_sum
    # the blurred mask reaches into dark surroundings, so only pixels bright for this LED give the color
    peak_b = np.append(0, smooth[py, px])
    colored = (owner >= 0) & ~np.all(rgb >= CLIP, axis=2) & (brightness >= COLOR_FRACTION * peak_b[owner + 1])
    if MIN_PEAK:  # the center is white, the color is in the glow around it: its most saturated pixels
        idx = (nearby + 1).ravel()
        sat = rgb.max(axis=2) - rgb.min(axis=2)
        max_sat = np.zeros(n)
        np.maximum.at(max_sat, idx, sat.ravel())
        colored = (nearby >= 0) & (sat >= 0.5 * max_sat[nearby + 1])
    colored = colored.ravel()
    n_colored = np.bincount(idx, colored, n)
    color_sum = np.stack([np.bincount(idx, rgb[..., c].ravel() * colored, n) for c in range(3)], axis=1)

    leds = []
    for i in range(1, n):
        if area[i] < MIN_AREA:
            continue
        led = {"x": cx[i], "y": cy[i], "area": int(area[i]), "rgb": None, "name": "clipped"}
        if n_colored[i]:
            led["rgb"] = color_sum[i] / n_colored[i]
            on = tuple(int(c >= ON_RATIO * led["rgb"].max()) for c in led["rgb"])
            led["name"] = NAMES.get(on, "?")
        leds.append(led)
    return sorted(leds, key=lambda led: (led["y"], led["x"]))


def print_leds(leds: list[dict]) -> None:
    print(f"\n{len(leds)} LEDs")
    print(f"{'#':>4} {'x':>7} {'y':>7} {'area':>5}   {'R':>5} {'G':>5} {'B':>5}   color")
    for i, led in enumerate(leds):
        r, g, b = ("-", "-", "-") if led["rgb"] is None else (f"{c:.0f}" for c in led["rgb"])
        print(f"{i:>4} {led['x']:7.1f} {led['y']:7.1f} {led['area']:5d}   {r:>5} {g:>5} {b:>5}   {led['name']}")


def led_radius(led: dict) -> int:
    """radius of a circle as big as the LED's light source."""
    return max(3, round(np.sqrt(led["area"] / np.pi)))


def draw_leds(frame: np.ndarray, leds: list[dict]) -> np.ndarray:
    out = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)  # gray, so the colored rings stand out
    thick = max(2, frame.shape[1] // 800)  # phone photos are shown shrunk, thin lines vanish
    for led in leds:
        center = (round(led["x"]), round(led["y"]))
        radius = led_radius(led) + thick  # just outside the light, so the ring does not cover it
        if led["rgb"] is None:
            color = (0, 0, 255)  # red ring = clipped
        else:
            r, g, b = led["rgb"] / max(led["rgb"].max(), 1) * 255  # full brightness so dim LEDs stay visible
            color = (int(b), int(g), int(r))
        cv2.circle(out, center, radius, color, thick)
    if len(leds) <= 50:  # names overlap on a long strip; press p for the list
        for led in leds:
            pos = (round(led["x"]) + led_radius(led) + 2 * thick, round(led["y"]) + 3 * thick)
            cv2.putText(out, led["name"], pos, cv2.FONT_HERSHEY_SIMPLEX, 0.25 * thick, (255, 255, 255), thick)
    cv2.putText(out, f"{len(leds)} LEDs", (20, 20 + 20 * thick), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * thick, (255, 255, 255), thick)
    return out


def strip_image(spacing: int, count: int) -> np.ndarray:
    """count LEDs (red, green, blue, ...) whose glows overlap, like a real strip."""
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0)]  # BGR
    img = np.zeros((100, 60 + spacing * count, 3), np.uint8)
    for i in range(count):  # glows first, so the cores sit on top
        cv2.circle(img, (30 + i * spacing, 50), 9, tuple(v // 4 for v in colors[i % 3]), -1)
    for i in range(count):
        cv2.circle(img, (30 + i * spacing, 50), 4, colors[i % 3], -1)
    return img


def selftest() -> None:
    img = np.zeros((200, 300, 3), np.uint8)
    cv2.circle(img, (50, 50), 6, (0, 0, 200), -1)  # red (BGR)
    cv2.circle(img, (150, 100), 6, (200, 200, 0), -1)  # cyan
    cv2.circle(img, (250, 150), 8, (0, 255, 0), -1)  # green with a clipped white core
    cv2.circle(img, (250, 150), 3, (255, 255, 255), -1)
    cv2.circle(img, (100, 150), 4, (255, 255, 255), -1)  # fully clipped
    cv2.circle(img, (270, 90), 4, (90, 0, 0), -1)  # dim blue
    img[10, 290] = 255  # single hot pixel = noise
    cv2.circle(img, (150, 30), 60, (40, 40, 120), -1)  # big glow on the wall ...
    cv2.circle(img, (150, 30), 6, (60, 60, 255), -1)  # ... with a red LED in it
    got = [(round(led["x"]), round(led["y"]), led["name"]) for led in find_leds(img)]
    want = [
        (150, 30, "red"),
        (50, 50, "red"),
        (270, 90, "blue"),
        (150, 100, "cyan"),
        (100, 150, "clipped"),
        (250, 150, "green"),
    ]
    assert got == want, got

    for spacing in (40, 16, 12):  # a strip of 60 LEDs, glows touching from 16 px on
        leds = sorted(find_leds(strip_image(spacing, 60)), key=lambda led: led["x"])
        got = [(round(led["x"]), led["name"]) for led in leds]
        want = [(30 + i * spacing, ("red", "green", "blue")[i % 3]) for i in range(60)]
        assert got == want, (spacing, got)

    global MIN_PEAK  # bright photo: light gray table, LEDs with white centers in colored glows
    img = np.full((100, 200, 3), 200, np.uint8)
    cv2.circle(img, (50, 50), 12, (255, 60, 255), -1)  # magenta glow (BGR)
    cv2.circle(img, (150, 50), 12, (60, 60, 255), -1)  # red glow
    for x in (50, 150):
        cv2.circle(img, (x, 50), 4, (255, 255, 255), -1)
    MIN_PEAK, keep = 215, MIN_PEAK
    try:
        got = [(round(led["x"]), round(led["y"]), led["name"]) for led in find_leds(img)]
    finally:
        MIN_PEAK = keep
    assert got == [(50, 50, "magenta"), (150, 50, "red")], got
    print("selftest ok")


IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Find LEDs and measure their colors.", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    p.add_argument("source", nargs="?", help="image or video file; camera if omitted")
    p.add_argument("--selftest", action="store_true", help="check detection on synthetic images")
    p.add_argument(
        "--threshold", type=float, default=THRESHOLD, help="how much brighter than its surroundings an LED is (0-255)"
    )
    p.add_argument("--led-size", type=int, default=LED_SIZE, help="px, larger than the biggest LED incl. halo")
    p.add_argument(
        "--spacing", type=int, default=SPACING, help="px, a bit less than the distance between neighboring LEDs"
    )
    p.add_argument("--blur", type=int, default=BLUR, help="px, odd")
    p.add_argument("--min-area", type=int, default=MIN_AREA, help="px, smaller = noise")
    p.add_argument(
        "--min-peak",
        type=float,
        default=MIN_PEAK,
        help="0-255, >0 = bright photo mode: LED centers are white, all channels at least this; ~215",
    )
    p.add_argument("--clip", type=int, default=CLIP, help="all channels at or above this = clipped")
    p.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="resize frames first (0.25 for big phone photos); sizes above are in scaled px, printed positions in original px",
    )
    p.add_argument("--out", help="save the annotated image (last frame for video/camera)")
    p.add_argument("--mask-out", help="save the detection mask (white = counts as LED)")
    p.add_argument("--no-show", action="store_true", help="no window, e.g. over ssh")
    args = p.parse_args()
    for name in ("led_size", "spacing", "blur"):  # OpenCV kernels need odd sizes
        if getattr(args, name) % 2 == 0:
            setattr(args, name, getattr(args, name) + 1)
    return args


def detect(frame: np.ndarray, scale: float) -> list[dict]:
    """find_leds on a resized frame, positions in original pixels."""
    if scale == 1:
        return find_leds(frame)
    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    leds = find_leds(small)
    for led in leds:
        led["x"], led["y"], led["area"] = led["x"] / scale, led["y"] / scale, round(led["area"] / scale**2)
    return leds


def mask_image(frame: np.ndarray, scale: float) -> np.ndarray:
    small = frame if scale == 1 else cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    mask = led_mask(small)[3].astype(np.uint8) * 255
    mask = cv2.resize(mask, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)


def show_fitted(image: np.ndarray) -> None:
    h, w = image.shape[:2]
    f = min(1.0, 1280 / w, 900 / h)  # phone photos are larger than the screen
    cv2.imshow("led colors", image if f == 1 else cv2.resize(image, None, fx=f, fy=f, interpolation=cv2.INTER_AREA))


def run_image(path: str, args: argparse.Namespace) -> None:
    frame = cv2.imread(path)
    if frame is None:
        sys.exit(f"Could not read image {path}")
    leds = detect(frame, args.scale)
    print_leds(leds)
    result = draw_leds(frame, leds)
    if args.out:
        cv2.imwrite(args.out, result)
    if args.mask_out:
        cv2.imwrite(args.mask_out, mask_image(frame, args.scale))
    if args.no_show:
        return
    show_mask = False
    while True:
        show_fitted(draw_leds(mask_image(frame, args.scale), leds) if show_mask else result)
        key = cv2.waitKey(0) & 0xFF
        if key in (ord("q"), 27):
            break
        elif key == ord("m"):
            show_mask = not show_mask
        elif key == ord("p"):
            print_leds(leds)
    cv2.destroyAllWindows()


def run_stream(args: argparse.Namespace) -> None:
    if args.source:
        cap = cv2.VideoCapture(args.source)
    else:
        from capture_manual import EXPOSURE, GAIN, apply_settings, open_camera

        cap = open_camera()
        apply_settings(cap, EXPOSURE, GAIN)
    if not cap.isOpened():
        sys.exit("Could not open video source")

    paused, show_mask, frame, leds = False, False, None, []
    while True:
        if not paused or frame is None:
            ok, frame_new = cap.read()
            if not ok:
                print("End of video")
                break
            frame = frame_new
            leds = detect(frame, args.scale)
            if args.no_show:
                print_leds(leds)
                continue
        view = mask_image(frame, args.scale) if show_mask else frame
        show_fitted(draw_leds(view, leds))
        key = cv2.waitKey(30) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("p"):
            print_leds(leds)
        elif key == ord("m"):
            show_mask = not show_mask
        elif key == ord(" "):
            paused = not paused
    if frame is not None and args.out:
        cv2.imwrite(args.out, draw_leds(frame, leds))
    if frame is not None and args.mask_out:
        cv2.imwrite(args.mask_out, mask_image(frame, args.scale))
    cap.release()
    cv2.destroyAllWindows()


def main() -> None:
    global THRESHOLD, LED_SIZE, SPACING, BLUR, MIN_AREA, CLIP, MIN_PEAK
    args = parse_args()
    if args.selftest:
        return selftest()
    THRESHOLD, LED_SIZE, SPACING = args.threshold, args.led_size, args.spacing
    BLUR, MIN_AREA, CLIP, MIN_PEAK = args.blur, args.min_area, args.clip, args.min_peak
    if args.source and Path(args.source).suffix.lower() in IMAGE_TYPES:
        run_image(args.source, args)
    else:
        run_stream(args)


if __name__ == "__main__":
    main()
