import argparse
from pathlib import Path

import cv2
import numpy as np

EXTS = {".png", ".jpg", ".jpeg"}

Led = tuple[int, int, tuple[int, int, int]]  # (cx, cy, average rgb 0-255)


def detect_leds(image, min_area: int = 50) -> list[Led]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (11, 11), 0)
    thresh = cv2.threshold(blurred, 200, 255, cv2.THRESH_BINARY)[1]
    thresh = cv2.erode(thresh, None, iterations=2)
    thresh = cv2.dilate(thresh, None, iterations=4)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh, connectivity=8)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    leds = []
    for label in range(1, num_labels):
        if stats[label, cv2.CC_STAT_AREA] <= min_area:
            continue
        pixels = rgb[labels == label]
        # overexposed LED cores read as white; colour lives in the unsaturated pixels around them
        colored = pixels[pixels.min(axis=1) < 220]
        avg = np.mean(colored if len(colored) else pixels, axis=0)
        cx, cy = centroids[label]
        leds.append((int(cx), int(cy), tuple(int(c) for c in avg)))
    return leds


def annotate(image, leds: list[Led]) -> None:
    scale = max(image.shape[:2]) / 1000  # text/markers grow with the image so they stay readable
    thick = max(1, round(2 * scale))
    for i, (cx, cy, rgb) in enumerate(leds):
        cv2.circle(image, (cx, cy), max(2, round(4 * scale)), (0, 0, 255), -1)
        org = (cx + round(10 * scale), cy)
        # black outline under the coloured text so it reads on any background
        cv2.putText(image, f"#{i}", org, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (0, 0, 0), thick * 3)
        cv2.putText(image, f"#{i}", org, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, rgb[::-1], thick)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="folder with LED images")
    ap.add_argument("--min-area", type=int, default=50, help="min blob size in pixels")
    ap.add_argument("--show", action="store_true", help="show annotated images")
    ap.add_argument("--out", default="led_colors.txt", help="txt file with one LED's color per image")
    ap.add_argument("--led-id", type=int, default=0, help="which detected LED (#n) to write to --out")
    args = ap.parse_args()

    files = sorted(p for p in Path(args.folder).iterdir() if p.suffix.lower() in EXTS)
    if not files:
        raise SystemExit(f"No images found in {args.folder}")

    with open(args.out, "w") as out:
        for path in files:
            image = cv2.imread(str(path))
            if image is None:
                print(f"{path.name}: could not load, skipped")
                continue

            leds = detect_leds(image, args.min_area)
            print(f"{path.name}: {len(leds)} LEDs")
            for i, (cx, cy, rgb) in enumerate(leds):
                print(f"  #{i} at ({cx}, {cy})  rgb={rgb}")

            if args.led_id < len(leds):  # one LED tracked across frames
                r, g, b = leds[args.led_id][2]
                out.write(f"{r}\t{g}\t{b}\n")
            else:
                out.write(f"{path.name}\tnone\n")

            if args.show:
                annotate(image, leds)
                cv2.namedWindow(path.name, cv2.WINDOW_NORMAL)  # resizable, big photos fit the screen
                cv2.imshow(path.name, image)
                cv2.waitKey(0)
                cv2.destroyAllWindows()
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
