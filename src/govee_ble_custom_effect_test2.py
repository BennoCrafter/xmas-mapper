import asyncio

from govee_ble_leds import GoveeStrip


async def main():
    start = (255, 0, 0)  # red
    end = (0, 0, 255)  # blue
    lights = 375

    print("Connecting...")
    async with GoveeStrip("6871", verbose=False) as strip:
        print("Connected.")

        await strip.power()
        strip.clear()
        await strip.show()

        for i in range(lights):
            strip.set([i], "#0000FF")
            # await strip.show(wait=True)

            t = i / (lights - 1)

            r = round(start[0] * (1 - t) + end[0] * t)
            g = round(start[1] * (1 - t) + end[1] * t)
            b = round(start[2] * (1 - t) + end[2] * t)

            strip.set([i], f"#{r:02X}{g:02X}{b:02X}")
        await strip.show(wait=True)


if __name__ == "__main__":
    asyncio.run(main())
