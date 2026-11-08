"""
LED ID Codec
"""

import sys

RGB = tuple[int, int, int]
Pair = tuple[RGB, RGB]  # (slot A, slot B = inverted A)


def print_color_block(text: object, rgb: RGB) -> None:
    r, g, b = (int(c * 255) for c in rgb)
    print(f"\x1b[48;2;{r};{g};{b}m    {text}    \x1b[0m")


class LedIdCodec:
    # only red and blue tell symbols apart: cameras leak blue into green, so yellow/cyan can't be read as digits
    SYMBOLS: list[RGB] = [
        (1, 0, 0),  # red (B slot: cyan)
        (0, 0, 1),  # blue (B slot: yellow)
        (1, 0, 1),  # magenta (B slot: green)
        (0, 1, 0),  # green (B slot: magenta)
    ]  # (never 000 or 111) (white or black)
    BASE = len(SYMBOLS)

    def __init__(self, n_leds: int) -> None:
        self.n_leds = n_leds
        self.n_digits = self.n_digits_for(n_leds)

    @classmethod
    def n_digits_for(cls, n_leds: int) -> int:  # how many base-4 digits are needed for n_leds IDs
        digits = 1
        while cls.BASE**digits < n_leds:
            digits += 1
        return digits  # e.g. 5 for 512 LEDs, because 4^5 = 1024 and 4^4 = 256

    def encode(self, led_id: int) -> list[Pair]:  # turn LED ID into array of (A, B(invert)) color pairs
        digits: list[int] = []
        for _ in range(self.n_digits):
            led_id, rest = divmod(led_id, self.BASE)  # rest = lowest digit, led_id = remaining value
            digits.append(rest)  # store the digit
        digits.append(sum(digits) % self.BASE)  # check digit: sum of all digits mod 4
        return [self._pair(self.SYMBOLS[d]) for d in digits]

    @staticmethod
    def _pair(color: RGB) -> Pair:  # slot A = digit's color, slot B = inverted color
        r, g, b = color
        return color, (1 - r, 1 - g, 1 - b)

    def decode(self, pairs: list[Pair]) -> int | None:  # turn color pairs back into an LED ID
        digits: list[int] = []

        for slot_a, slot_b in pairs:  # go through each Manchester pair
            r, b = (int(slot_a[c] > slot_b[c]) for c in (0, 2))  # red/blue brighter in A than B -> 1 else -> 0
            digits.append([(s[0], s[2]) for s in self.SYMBOLS].index((r, b)))  # color pattern -> digit 0-3
        data, check = digits[:-1], digits[-1]  # split data digits and check digit

        if sum(data) % self.BASE != check:  # recompute check digit and compare
            return None  # mismatch -> transmission error -> discard

        return sum(d * self.BASE**i for i, d in enumerate(data))  # base-4 digits -> integer ID


if __name__ == "__main__":
    codec = LedIdCodec(n_leds=512)  # number of LEDs on the strip

    pairs = codec.encode(int(sys.argv[1]) if len(sys.argv) > 1 else 11)  # LED id, or "11" as default

    for i, (slot_a, slot_b) in enumerate(pairs):  # show every pair
        print(f"Pair {i}:")  # pair number
        print_color_block(slot_a, slot_a)  # color in slot A
        print_color_block(slot_b, slot_b)  # color in slot B

    print("Decoded:", codec.decode(pairs))
    assert all(codec.decode(codec.encode(n)) == n for n in range(512))
