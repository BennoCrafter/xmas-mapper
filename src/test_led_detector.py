import tempfile
from pathlib import Path

import cv2
import numpy as np

from led_detector import Tracker, changed, detect_leds, frames

RGB = (255, 0, 0)


def test_tracker():
    t = Tracker(max_distance=20, max_missed=2, min_hits=3)
    assert t.update([(100, 100, RGB), (300, 100, RGB)]) == {}
    assert t.update([(300, 100, RGB), (105, 100, RGB)]) == {}  # order swapped, blobs moving
    ids = t.update([(110, 100, RGB), (500, 500, RGB), (300, 100, RGB)])  # plus one-frame noise
    still, moving = (0, 1) if ids[0][0] == 300 else (1, 0)
    assert sorted(ids) == [0, 1] and ids[moving][0] > 100
    assert t.update([(300, 100, RGB)]) == {still: (300, 100, RGB)}  # moving one coasts
    assert moving in t.update([(120, 100, RGB), (300, 100, RGB)])  # re-acquired near its prediction
    for _ in range(3):
        t.update([])
    assert t.tracks == []  # everything exceeded max_missed


def test_video():
    """Two LEDs drift across a synthetic clip, one blinks off; both keep their id."""
    path = str(Path(tempfile.mkdtemp()) / "leds.mp4")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for n in range(20):
        img = np.zeros((240, 320, 3), np.uint8)
        cv2.circle(img, (40 + 5 * n, 80), 12, (255, 255, 255), -1)
        if not 8 <= n < 11:  # second LED off for 3 frames
            cv2.circle(img, (200, 60 + 4 * n), 12, (255, 255, 255), -1)
        writer.write(img)
    writer.release()

    t = Tracker(max_distance=20)
    seen = [t.update(detect_leds(img)) for _, img in frames(path)]
    assert len(seen) == 20
    assert all(s.keys() == {0, 1} for s in seen[2:8] + seen[11:])  # same two ids before and after the gap
    assert all(len(s) == 1 for s in seen[8:11])


if __name__ == "__main__":
    test_tracker()
    test_video()
    print("ok")


def test_changed():
    assert changed((255, 0, 0), (250, 0, 0), None)
    assert not changed((255, 0, 0), (250, 0, 0), 10)
    assert changed((255, 0, 0), (240, 0, 0), 10)
    assert changed((255, 0, 0), None, 10)
    assert changed("none", (255, 0, 0), 10) and not changed("none", "none", 10)
