import time

import requests

WLED_IP = "192.168.178.85"

# -----------------------------
# Hamming(7,4) ENCODE / DECODE
# -----------------------------


def hamming74_encode(n):
    d = [(n >> 3) & 1, (n >> 2) & 1, (n >> 1) & 1, n & 1]
    d1, d2, d3, d4 = d
    p1 = d1 ^ d2 ^ d4
    p2 = d1 ^ d3 ^ d4
    p3 = d2 ^ d3 ^ d4
    return [p1, p2, d1, p3, d2, d3, d4]


def hamming74_decode(bits):
    p1, p2, d1, p3, d2, d3, d4 = bits
    c1 = p1 ^ d1 ^ d2 ^ d4
    c2 = p2 ^ d1 ^ d3 ^ d4
    c3 = p3 ^ d2 ^ d3 ^ d4
    error = (c3 << 2) | (c2 << 1) | c1
    if 1 <= error <= 7:
        bits[error - 1] ^= 1
        p1, p2, d1, p3, d2, d3, d4 = bits
    return (d1 << 3) | (d2 << 2) | (d3 << 1) | d4


# -----------------------------
# Split / Combine Nibbles
# -----------------------------


def split_to_nibbles(n):
    if n == 0:
        return [0]
    nibbles = []
    while n > 0:
        nibbles.append(n & 0xF)
        n >>= 4
    return list(reversed(nibbles))


def combine_nibbles(nibbles):
    n = 0
    for nib in nibbles:
        n = (n << 4) | nib
    return n


# -----------------------------
# BIT ↔ COLOR (Lossless)
# -----------------------------

BIT0 = [255, 0, 0]  # Red
BIT1 = [0, 0, 255]  # Blue


def bits_to_colors(bits):
    return [BIT1 if b == 1 else BIT0 for b in bits]


def color_to_bit(color):
    if color == BIT0:
        return 0
    elif color == BIT1:
        return 1
    else:
        raise ValueError("Invalid color – only Red/Blue allowed")


def colors_to_bits(colors):
    return [color_to_bit(c) for c in colors]


# -----------------------------
# WLED CONTROL
# -----------------------------


def send_color(color, duration=0.1, turn_off=False):
    # Set whole strip (or first LED) to color
    data = {
        "seg": [
            {
                "i": [color]  # first LED only; or expand to more
            }
        ],
        "on": True,
        "bri": 255,
    }
    requests.post(f"http://{WLED_IP}/json/state", json=data)
    time.sleep(duration)

    if turn_off:
        # then turn off (black)
        data["seg"][0]["i"][0] = [0, 0, 0]
        requests.post(f"http://{WLED_IP}/json/state", json=data)


def blink_number(n):
    nibbles = split_to_nibbles(n)
    encoded = [hamming74_encode(x) for x in nibbles]
    print("Nibbles:", nibbles)
    print("Encoded:", encoded)

    # Flatten list of colors
    all_colors = [color for block in encoded for color in bits_to_colors(block)]

    for color in all_colors:
        send_color(color)
        send_color([255, 255, 255])

    send_color([255, 255, 255], turn_off=True)  # separator

    return all_colors, len(encoded)


def decode_colors(colors, block_count):
    bits = colors_to_bits(colors)
    decoded_nibbles = []
    for i in range(block_count):
        block = bits[i * 7 : (i + 1) * 7]
        decoded_nibbles.append(hamming74_decode(block))
    return combine_nibbles(decoded_nibbles)


# -----------------------------
# MAIN
# -----------------------------


def colorName(color):
    r, g, b = color
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
    return "unknown"


if __name__ == "__main__":
    number = 4
    print("Sending:", number)
    colors, blocks = blink_number(number)
    print(colors, blocks)
    for color in colors:
        print(colorName(color))
    print([colorName(color) for color in colors])
    recovered = decode_colors(colors, blocks)
    print("Recovered:", recovered)
