import time

import requests

WLED_IP = "192.168.178.85"


# -----------------------------
# LED control functions
# -----------------------------
def send_color(color: list[int], turn_off=False):
    data = {
        "seg": [{"i": [color]}],
        "on": True,
        "bri": 255,
    }
    try:
        requests.post(f"http://{WLED_IP}/json/state", json=data, timeout=0.5)
    except requests.RequestException as e:
        print(f"Error sending color: {e}")

    if turn_off:
        data["seg"][0]["i"][0] = [0, 0, 0]
        try:
            requests.post(f"http://{WLED_IP}/json/state", json=data, timeout=0.5)
        except requests.RequestException as e:
            print(f"Error turning off LED: {e}")


def turn_off():
    send_color([0, 0, 0], turn_off=True)


def send_rgb(r: int, g: int, b: int, turn_off=False):
    send_color([r, g, b], turn_off)


time_delay = 0.5

send_rgb(255, 0, 255)
time.sleep(time_delay)
send_rgb(0, 255, 0)
time.sleep(time_delay)
send_rgb(0, 0, 255)
time.sleep(time_delay)
send_rgb(255, 255, 0)
time.sleep(time_delay)
send_rgb(255, 255, 0)
time.sleep(time_delay)
send_rgb(0, 0, 255)
time.sleep(time_delay)
send_rgb(0, 255, 0)
time.sleep(time_delay)
send_rgb(255, 0, 255)
time.sleep(time_delay)
send_rgb(255, 0, 255)
time.sleep(time_delay)
send_rgb(0, 255, 0)
time.sleep(time_delay)
turn_off()
