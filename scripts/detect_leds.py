import cv2
import numpy as np


def configure_camera(cap):
    # Disable auto-exposure and set manual exposure
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # 0.25 = manual mode for some cameras
    cap.set(cv2.CAP_PROP_EXPOSURE, -6)  # adjust as needed
    cap.set(cv2.CAP_PROP_GAIN, 0)


def merge_overlapping_circles(circles, merge_distance=30):
    merged = []
    while circles:
        x, y, r = circles.pop(0)
        close = [(x, y, r)]
        rest = []
        for cx, cy, cr in circles:
            distance = np.hypot(cx - x, cy - y)
            if distance < merge_distance:
                close.append((cx, cy, cr))
            else:
                rest.append((cx, cy, cr))
        circles = rest
        # Keep the circle with the largest radius
        largest = max(close, key=lambda c: c[2])
        merged.append(largest)
    return merged


colors = []


def get_basic_color(bgr_color):
    b, g, r = bgr_color
    max_val = max(r, g, b)
    min_val = min(r, g, b)

    if max_val < 50:
        return "black"
    if max_val > 200 and (max_val - min_val) < 50:
        return "white"
    if r > g + 50 and r > b + 50:
        return "red"
    if g > r + 50 and g > b + 50:
        return "green"
    if b > r + 50 and b > g + 50:
        return "blue"
    return "white"


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Cannot open camera")
        return

    configure_camera(cap)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame")
            break

        # Convert to grayscale and threshold
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, bw = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)
        bw = cv2.medianBlur(bw, 9)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))

        # Find contours and approximate circles
        contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        circles = []
        for cnt in contours:
            (x, y), radius = cv2.minEnclosingCircle(cnt)
            if radius < 5:  # ignore small noise
                continue
            circles.append((int(x), int(y), int(radius)))

        merged_circles = merge_overlapping_circles(circles, merge_distance=30)

        # Detect color for each circle
        for x, y, r in merged_circles:
            roi = frame[max(y - r, 0) : y + r, max(x - r, 0) : x + r]
            if roi.size < 4:
                continue
            mean_color = cv2.mean(roi)[:3]
            color_name = get_basic_color(mean_color)
            if len(colors) == 0 or color_name != colors[-1]:
                colors.append(color_name)

            # Draw circle and label
            cv2.circle(frame, (x, y), r + 15, (0, 0, 255), 3)
            cv2.putText(
                frame,
                color_name,
                (x - r, y - r - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )

        cv2.imshow("BW Mask", bw)
        cv2.imshow("LED Detection (Color)", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    for c in colors:
        if c != "white":
            print(c)
    print([c for c in colors if c != "white"])


if __name__ == "__main__":
    main()
