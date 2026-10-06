def print_color_block(text, rgb):
    r, g, b = (int(c * 255) for c in rgb)
    print(f"\x1b[48;2;{r};{g};{b}m    {text}    \x1b[0m")


SYMBOLS = [
    (1, 0, 0),  # red
    (0, 1, 0),  # green
    (0, 0, 1),  # blue
    (1, 1, 0),  # yellow
    (1, 0, 1),  # magenta
    (0, 1, 1),  # cyan
]  # (never 000 or 111) (white or black)
BASE = len(SYMBOLS)


def n_digits_for(n_leds: int) -> int:  # how many base-6 digits are needed for n_leds IDs
    digits = 1
    while BASE**digits < n_leds:
        digits += 1
    return digits  # e.g. 4 for 512 LEDs, because 6^4 = 1296


def encode(led_id: int, n_digits: int):  # turn LED ID into array of (A, B(invert)) color pairs
    digits = []
    for _ in range(n_digits):
        led_id, rest = divmod(led_id, BASE)  # rest = lowest digit, led_id = remaining value
        digits.append(rest)  # store the digit
    digits.append(sum(digits) % BASE)  # check digit: sum of all digits mod 6
    return [
        (
            SYMBOLS[d],
            tuple(1 - x for x in SYMBOLS[d]),
        )  # slot A = digit's color, slot B = inverted color
        for d in digits
    ]


def decode(
    pairs: list[tuple[tuple[int, int, int], tuple[int, int, int]]], n_digits: int
) -> int | None:  # turn color pairs back into an LED ID (None on error)
    digits: list[int] = []

    for slot_a, slot_b in pairs:  # go through each Manchester pair
        pattern: tuple = tuple(
            int(a > b) for a, b in zip(slot_a, slot_b)
        )  # if channel brighter in A than B -> 1 else -> 0

        if pattern not in SYMBOLS:  # 000 or 111 can never be sent...
            return None  # wrong pattern

        digits.append(SYMBOLS.index(pattern))  # color pattern -> digit 0-5
    data, check = digits[:-1], digits[-1]  # split data digits and check digit

    if sum(data) % BASE != check:  # recompute check digit and compare
        return None  # mismatch -> transmission error -> discard

    return sum(d * BASE**i for i, d in enumerate(data))  # base-6 digits -> integer ID


n_leds = 512  # number of LEDs on the strip
n_digits = n_digits_for(n_leds)

pairs = encode(340, n_digits)

for i, (slot_a, slot_b) in enumerate(pairs):  # show every pair
    print(f"Pair {i}:")  # pair number
    print_color_block(slot_a, slot_a)  # color in slot A
    print_color_block(slot_b, slot_b)  # color in slot B

print("Decoded:", decode(pairs, n_digits))
