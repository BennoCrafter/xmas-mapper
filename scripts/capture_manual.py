"""
Capture video with manual camera settings for LED mapping.

Keys while the preview window is focused:
    r        start / stop recording
    + / -    longer / shorter exposure
    g / h    more / less gain
    q        quit

Clipped pixels (all channels >= 250) are shown in red in the preview only,
the recorded video stays untouched.
"""

import csv
import sys
import time

import cv2
import numpy as np

# ---------------------------------------------------------------- settings
CAMERA_INDEX = 0  # 0 = first camera, try 1, 2 ... for others
WIDTH, HEIGHT, FPS = 1920, 1080, 30  # requested resolution and frame rate
GAIN = 0  # sensor gain (≈ ISO), keep as low as possible
WB_TEMPERATURE = 5000  # fixed white balance in Kelvin (daylight ≈ 5000-5500)
FOCUS = 30  # manual focus value, depends on the camera
OUTPUT_VIDEO = "capture.mp4"
OUTPUT_TIMESTAMPS = "capture_timestamps.csv"

# Exposure values mean different things on different backends
if sys.platform.startswith("win"):
    BACKEND = cv2.CAP_DSHOW
    AUTO_EXPOSURE_OFF = 0.25  # DirectShow: 0.25 = manual, 0.75 = auto
    EXPOSURE = -7  # log2 seconds: -7 ≈ 1/128 s, -8 ≈ 1/256 s
    EXPOSURE_STEP = 1
elif sys.platform.startswith("linux"):
    BACKEND = cv2.CAP_V4L2
    AUTO_EXPOSURE_OFF = 1  # V4L2: 1 = manual, 3 = auto
    EXPOSURE = 80  # units of 100 µs: 80 ≈ 1/125 s
    EXPOSURE_STEP = 10
else:
    BACKEND = cv2.CAP_ANY  # macOS ignores most manual settings
    AUTO_EXPOSURE_OFF = 0.25
    EXPOSURE = -7
    EXPOSURE_STEP = 1


def open_camera() -> cv2.VideoCapture:
    cap = cv2.VideoCapture(CAMERA_INDEX, BACKEND)
    if not cap.isOpened():
        sys.exit(f"Could not open camera {CAMERA_INDEX}")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # needed for 1080p30 on many webcams
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always get the newest frame
    return cap


def apply_settings(cap: cv2.VideoCapture, exposure: float, gain: float) -> None:
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_OFF)  # must come before EXPOSURE
    cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
    cap.set(cv2.CAP_PROP_GAIN, gain)
    cap.set(cv2.CAP_PROP_AUTO_WB, 0)
    cap.set(cv2.CAP_PROP_WB_TEMPERATURE, WB_TEMPERATURE)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
    cap.set(cv2.CAP_PROP_FOCUS, FOCUS)


def print_settings(cap: cv2.VideoCapture) -> None:
    # Read back what the camera actually accepted (unsupported settings are silently ignored)
    props = {
        "width": cv2.CAP_PROP_FRAME_WIDTH,
        "height": cv2.CAP_PROP_FRAME_HEIGHT,
        "fps": cv2.CAP_PROP_FPS,
        "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
        "exposure": cv2.CAP_PROP_EXPOSURE,
        "gain": cv2.CAP_PROP_GAIN,
        "auto_wb": cv2.CAP_PROP_AUTO_WB,
        "wb_temperature": cv2.CAP_PROP_WB_TEMPERATURE,
        "autofocus": cv2.CAP_PROP_AUTOFOCUS,
        "focus": cv2.CAP_PROP_FOCUS,
    }
    for name, prop in props.items():
        print(f"  {name:15s} {cap.get(prop)}")


def draw_overlay(frame: np.ndarray, exposure: float, gain: float, recording: bool) -> np.ndarray:
    preview = frame.copy()
    clipped = np.all(frame >= 250, axis=2)  # pixels where every channel is maxed out
    preview[clipped] = (0, 0, 255)  # mark them red
    status = "REC" if recording else "preview"
    text = f"{status}  exposure {exposure}  gain {gain}  clipped px {int(clipped.sum())}"
    cv2.putText(preview, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    return preview


def main() -> None:
    cap = open_camera()
    exposure, gain = EXPOSURE, GAIN
    apply_settings(cap, exposure, gain)
    print("Camera settings after applying:")
    print_settings(cap)

    real_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    real_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    real_fps = cap.get(cv2.CAP_PROP_FPS) or FPS

    writer, ts_file, ts_writer = None, None, None
    frame_index, t_start = 0, 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Frame grab failed")
            break
        t_now = time.monotonic()

        if writer is not None:
            writer.write(frame)  # record the raw frame, without overlay
            ts_writer.writerow([frame_index, f"{(t_now - t_start) * 1000:.2f}"])
            frame_index += 1

        cv2.imshow("capture", draw_overlay(frame, exposure, gain, writer is not None))
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("r"):
            if writer is None:
                writer = cv2.VideoWriter(OUTPUT_VIDEO, cv2.VideoWriter_fourcc(*"mp4v"), real_fps, (real_w, real_h))
                ts_file = open(OUTPUT_TIMESTAMPS, "w", newline="")
                ts_writer = csv.writer(ts_file)
                ts_writer.writerow(["frame", "time_ms"])
                frame_index, t_start = 0, time.monotonic()
                print("Recording started")
            else:
                writer.release()
                ts_file.close()
                writer = None
                print(f"Recording saved: {OUTPUT_VIDEO}, {OUTPUT_TIMESTAMPS}")
        elif key in (ord("+"), ord("=")):
            exposure += EXPOSURE_STEP
            apply_settings(cap, exposure, gain)
        elif key == ord("-"):
            exposure -= EXPOSURE_STEP
            apply_settings(cap, exposure, gain)
        elif key == ord("g"):
            gain += 1
            apply_settings(cap, exposure, gain)
        elif key == ord("h"):
            gain = max(0, gain - 1)
            apply_settings(cap, exposure, gain)

    if writer is not None:
        writer.release()
        ts_file.close()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
