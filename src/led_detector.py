import argparse
import json
import shutil
import tempfile
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

EXTS = {".png", ".jpg", ".jpeg"}

Led = tuple[int, int, tuple[int, int, int]]  # (cx, cy, average rgb 0-255)


def detect_leds(image, min_area: int = 50, stages: dict | None = None) -> list[Led]:
    """Pass a dict as stages to get every intermediate image of the pipeline (for the viewer)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (11, 11), 0)
    thresh = cv2.threshold(blurred, 200, 255, cv2.THRESH_BINARY)[1]
    eroded = cv2.erode(thresh, None, iterations=2)
    dilated = cv2.dilate(eroded, None, iterations=4)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(dilated, connectivity=8)
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

    if stages is not None:
        kept = [label for label in range(1, num_labels) if stats[label, cv2.CC_STAT_AREA] > min_area]
        blobs = np.isin(labels, kept).astype(np.uint8) * 255  # only blobs big enough to count as LEDs
        stages.update(gray=gray, blurred=blurred, threshold=thresh, eroded=eroded, dilated=dilated, blobs=blobs)
    return leds


class Tracker:
    """Keeps LED ids stable across frames: predict, greedily match nearest pairs, coast, confirm."""

    def __init__(self, max_distance: float = 30, max_missed: int = 5, min_hits: int = 3, alpha: float = 0.5):
        self.max_distance = max_distance  # a bit above the fastest per-frame blob movement
        self.max_missed = max_missed  # frames a track may coast without a detection
        self.min_hits = min_hits  # detections before a track gets an id
        self.alpha = alpha  # weight of the new detection; lower = smoother
        self.tracks: list[dict] = []
        self.next_id = 0

    def update(self, leds: list[Led]) -> dict[int, Led]:
        """Returns confirmed tracks detected in this frame, keyed by stable id."""
        preds = [(t["x"] + t["vx"], t["y"] + t["vy"]) for t in self.tracks]
        pairs = sorted(
            (np.hypot(px - cx, py - cy), ti, di)
            for ti, (px, py) in enumerate(preds)
            for di, (cx, cy, _) in enumerate(leds)
        )
        matched_t, matched_d, seen = set(), set(), {}
        for dist, ti, di in pairs:
            if dist > self.max_distance:
                break
            if ti in matched_t or di in matched_d:
                continue
            matched_t.add(ti)
            matched_d.add(di)
            t, (px, py), (cx, cy, rgb) = self.tracks[ti], preds[ti], leds[di]
            x, y = self.alpha * cx + (1 - self.alpha) * px, self.alpha * cy + (1 - self.alpha) * py
            t.update(vx=x - t["x"], vy=y - t["y"], x=x, y=y, hits=t["hits"] + 1, missed=0)
            if t["id"] is None and t["hits"] >= self.min_hits:
                t["id"], self.next_id = self.next_id, self.next_id + 1
            if t["id"] is not None:
                seen[t["id"]] = (round(x), round(y), rgb)

        for ti, t in enumerate(self.tracks):
            if ti not in matched_t:  # coast along the prediction
                t.update(x=preds[ti][0], y=preds[ti][1], missed=t["missed"] + 1)
        self.tracks = [t for t in self.tracks if t["missed"] <= self.max_missed]
        for di, (cx, cy, rgb) in enumerate(leds):
            if di not in matched_d:
                self.tracks.append(dict(id=None, x=cx, y=cy, vx=0.0, vy=0.0, hits=1, missed=0))
                if self.min_hits <= 1:
                    self.tracks[-1]["id"], self.next_id = self.next_id, self.next_id + 1
                    seen[self.tracks[-1]["id"]] = (cx, cy, rgb)
        return seen


def save_frame(folder: Path, n: int, images: dict) -> dict[str, str]:
    """Writes one jpeg per pipeline stage, returns {stage: file}."""
    files = {}
    for stage, image in images.items():
        scale = min(1, 1600 / max(image.shape[:2]))  # small jpegs keep the browser fast; LED coords stay in source px
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        files[stage] = f"{n:05d}_{stage}.jpg"
        cv2.imwrite(str(folder / files[stage]), small, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return files


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the terminal free of request logs
        pass


def serve(folder: Path, shown: list[dict], threshold: int | None = None) -> None:
    """Serves led_viewer.html with the saved frames on localhost until Ctrl+C."""
    (folder / "frames.json").write_text(json.dumps(shown))
    shutil.copy(Path(__file__).with_name("led_viewer.html"), folder / "index.html")
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(folder))) as server:
        url = f"http://127.0.0.1:{server.server_port}/" + ("" if threshold is None else f"?threshold={threshold}")
        print(f"Viewer at {url}  (Ctrl+C to stop)")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def changed(rgb, last, threshold: int | None) -> bool:
    """True if rgb differs from last by more than threshold in any channel (None = every frame counts)."""
    if threshold is None or last is None or "none" in (rgb, last):
        return threshold is None or rgb != last
    return max(abs(a - b) for a, b in zip(rgb, last)) > threshold


def frames(source: str, fps: float = 5):
    """Yields (name, image) from a folder of images or a video file sampled at fps; image is None if unreadable."""
    path = Path(source)
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in EXTS)
        if not files:
            raise SystemExit(f"No images found in {source}")
        for p in files:
            yield p.name, cv2.imread(str(p))
        return
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"Could not open video {source}")
    step = max(1, round((cap.get(cv2.CAP_PROP_FPS) or fps) / fps))  # e.g. 30 fps video -> every 6th frame
    n = 0
    while cap.grab():  # grab without decoding, only sampled frames get decoded
        if n % step == 0:
            yield f"frame{n:05d}", cap.retrieve()[1]
        n += 1
    cap.release()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="folder with LED images, or a video file")
    ap.add_argument("--min-area", type=int, default=50, help="min blob size in pixels")
    ap.add_argument("--show", action="store_true", help="open the results in a browser viewer")
    ap.add_argument("--out", default="led_colors.txt", help="txt file with one LED's color per image")
    ap.add_argument("--led-id", type=int, default=0, help="which tracked LED (#n) to write to --out")
    ap.add_argument("--max-distance", type=float, default=30, help="max px a blob moves between frames")
    ap.add_argument("--fps", type=float, default=10, help="frames per second to sample from a video")
    ap.add_argument("--min-hits", type=int, default=3, help="frames before a blob gets an id (1 = no noise filter)")
    ap.add_argument(
        "--threshold", type=int, help="only write a color when a channel changes by more than this (default: every frame)"
    )
    args = ap.parse_args()

    tracker = Tracker(max_distance=args.max_distance, min_hits=args.min_hits)
    shown: list[dict] = []
    last = None  # last color written to --out
    with tempfile.TemporaryDirectory() as tmp:
        with open(args.out, "w", buffering=1) as out:  # line-buffered: rows survive a hard quit
            for name, image in frames(args.folder, args.fps):
                if image is None:
                    print(f"{name}: could not load, skipped")
                    continue

                stages = {"original": image} if args.show else None
                detections = detect_leds(image, args.min_area, stages)
                leds = tracker.update(detections)
                # print(f"{name}: {len(leds)} LEDs")
                # for i, (cx, cy, rgb) in leds.items():
                # print(f"  #{i} at ({cx}, {cy})  rgb={rgb}")

                rgb = tuple(leds[args.led_id][2]) if args.led_id in leds else "none"  # one LED tracked across frames
                if changed(rgb, last, args.threshold):
                    out.write("\t".join(map(str, rgb)) + "\n" if rgb != "none" else f"{name}\tnone\n")
                    last = rgb

                if args.show:
                    h, w = image.shape[:2]
                    files = save_frame(Path(tmp), len(shown), stages)
                    shown.append(
                        dict(
                            name=name,
                            files=files,
                            w=w,
                            h=h,
                            leds=[dict(id=i, x=x, y=y, rgb=rgb) for i, (x, y, rgb) in leds.items()],
                            raw=[[x, y] for x, y, _ in detections],  # every blob, tracked or not
                        )
                    )
        print(f"Wrote {args.out}")
        if args.show:
            serve(Path(tmp), shown, args.threshold)


if __name__ == "__main__":
    main()
