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
    SYMBOLS: list[RGB] = [
        (1, 0, 0),  # red
        (0, 1, 0),  # green
        (0, 0, 1),  # blue
        (1, 1, 0),  # yellow
        (1, 0, 1),  # magenta
        (0, 1, 1),  # cyan
    ]  # (never 000 or 111) (white or black)
    BASE = len(SYMBOLS)

    def __init__(self, n_leds: int) -> None:
        self.n_leds = n_leds
        self.n_digits = self.n_digits_for(n_leds)

    @classmethod
    def n_digits_for(cls, n_leds: int) -> int:  # how many base-6 digits are needed for n_leds IDs
        digits = 1
        while cls.BASE**digits < n_leds:
            digits += 1
        return digits  # e.g. 4 for 512 LEDs, because 6^4 = 1296 and 6^3 = 216

    def encode(self, led_id: int) -> list[Pair]:  # turn LED ID into array of (A, B(invert)) color pairs
        digits: list[int] = []
        for _ in range(self.n_digits):
            led_id, rest = divmod(led_id, self.BASE)  # rest = lowest digit, led_id = remaining value
            digits.append(rest)  # store the digit
        digits.append(sum(digits) % self.BASE)  # check digit: sum of all digits mod 6
        return [self._pair(self.SYMBOLS[d]) for d in digits]

    @staticmethod
    def _pair(color: RGB) -> Pair:  # slot A = digit's color, slot B = inverted color
        r, g, b = color
        return color, (1 - r, 1 - g, 1 - b)

    def decode(self, pairs: list[Pair]) -> int | None:  # turn color pairs back into an LED ID
        digits: list[int] = []

        for slot_a, slot_b in pairs:  # go through each Manchester pair
            r, g, b = (int(a > b) for a, b in zip(slot_a, slot_b))  # if channel brighter in A than B -> 1 else -> 0
            pattern: RGB = (r, g, b)

            if pattern not in self.SYMBOLS:  # 000 or 111 can never be sent...
                return None  # wrong pattern

            digits.append(self.SYMBOLS.index(pattern))  # color pattern -> digit 0-5
        data, check = digits[:-1], digits[-1]  # split data digits and check digit

        if sum(data) % self.BASE != check:  # recompute check digit and compare
            return None  # mismatch -> transmission error -> discard

        return sum(d * self.BASE**i for i, d in enumerate(data))  # base-6 digits -> integer ID


if __name__ == "__main__":
    codec = LedIdCodec(n_leds=512)  # number of LEDs on the strip

    pairs = codec.encode(int(sys.argv[1]) if len(sys.argv) > 1 else 11)  # LED id, or "11" as default

    for i, (slot_a, slot_b) in enumerate(pairs):  # show every pair
        print(f"Pair {i}:")  # pair number
        print_color_block(slot_a, slot_a)  # color in slot A
        print_color_block(slot_b, slot_b)  # color in slot B

    print("Decoded:", codec.decode(pairs))
    assert all(codec.decode(codec.encode(n)) == n for n in range(512))
