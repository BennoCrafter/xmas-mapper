import asyncio

from govee_strip import GoveeStrip
from led_id_codec import encode, n_digits_for


async def main(n: int = 0):
    print("Connecting...")
    async with GoveeStrip("6871", verbose=False) as strip:
        print("Connected.")

        await strip.power()
        strip.clear()
        await strip.show()

        encoded = encode(n, n_digits_for(375))
        for i, (slot_a, slot_b) in enumerate(encoded):
            print(f"Blinking pair {i}")  # pair number

            await blink_color(strip, n, slot_a)
            await blink_color(strip, n, slot_b)


async def blink_color(strip: GoveeStrip, n: int, color: tuple[int, int, int], duration: float = 0.5):
    r, g, b = (int(c * 255) for c in color)

    strip.set([n], f"#{r:02X}{g:02X}{b:02X}")
    await strip.show(wait=True)
    await asyncio.sleep(duration)


if __name__ == "__main__":
    asyncio.run(main(n=11))
