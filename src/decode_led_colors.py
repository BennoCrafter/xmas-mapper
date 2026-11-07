import argparse

from led_id_codec import LedIdCodec


def parse_pairs(text: str) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Text contains lines of 'R G B' (slot A, slot B)"""
    lines = text.strip().splitlines()
    pairs = []
    for n in range(0, len(lines) - 1, 2):
        rows = [tuple(int(v) for v in line.split()) for line in lines[n : n + 2]]
        if len(rows) != 2 or any(len(r) != 3 for r in rows):
            raise ValueError(f"pair {n}: expected 2 lines of 'R G B', got {rows}")
        pairs.append((rows[0], rows[1]))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="Decode an LED ID from measured color pairs")
    ap.add_argument("file", nargs="?", default="led_colors.txt", help="txt file with RGB pairs")
    args = ap.parse_args()

    with open(args.file) as f:
        pairs = parse_pairs(f.read())
    led_id = LedIdCodec(n_leds=1).decode(pairs)  # decode only needs the pairs, n_leds is unused
    if led_id is None:
        raise SystemExit("Could not decode: invalid color pattern or check digit mismatch")
    print(led_id)


if __name__ == "__main__":
    main()
