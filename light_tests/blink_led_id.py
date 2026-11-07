import asyncio
import sys

from govee_strip import TOTAL_LEDS, GoveeStrip
from led_id_codec import RGB, LedIdCodec


async def main(ids: list[int], duration: float = 1, repeats: int = 1):
    print("Connecting...")
    async with GoveeStrip("6871", verbose=False) as strip:
        print("Connected.")

        strip.set_brightness(30)
        await strip.power()
        strip.clear()
        await strip.show()

        codec = LedIdCodec(n_leds=TOTAL_LEDS)
        encoded = {n: codec.encode(n) for n in ids}  # every LED blinks its own ID at the same time

        for _ in range(repeats):
            for i in range(codec.n_digits + 1):  # data digits + check digit
                print(f"Blinking pair {i}")  # pair number

                for slot in (0, 1):  # slot A, then slot B
                    for n, pairs in encoded.items():
                        set_color(strip, n, pairs[i][slot])
                    await strip.show(wait=True)
                    await asyncio.sleep(duration)


def set_color(strip: GoveeStrip, n: int, color: RGB):
    r, g, b = (int(c * 255) for c in color)
    strip.set([n], f"#{r:02X}{g:02X}{b:02X}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "11"  # LED id, or "all"
    asyncio.run(main(list(range(TOTAL_LEDS)) if arg == "all" else [int(arg)]))
