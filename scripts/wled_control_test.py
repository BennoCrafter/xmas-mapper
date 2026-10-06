import time

import numpy as np
import requests

WLED_IP = "192.168.178.85"


# -----------------------------
# LED control functions
# -----------------------------
def send_color(color, turn_off=False):
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


def send_rgb(r, g, b, turn_off=False):
    send_color([r, g, b], turn_off)


"""
[0, 1, 0] → Only Green is ON
[0, 1, 1] → Green + Blue are ON
[1, 0, 0] → Only Red is ON
[1, 0, 1] → Red + Blue are ON
"""
send_rgb(0, 255, 0)
time.sleep(2)
send_rgb(0, 255, 255)
time.sleep(2)
send_rgb(255, 0, 0)
time.sleep(2)
send_rgb(255, 0, 255)
time.sleep(2)
