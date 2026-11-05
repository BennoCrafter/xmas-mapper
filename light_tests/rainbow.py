import asyncio
import colorsys

from govee_strip import GoveeStrip


async def main(animate: bool = True):
    lights = 375

    print("Connecting...")
    async with GoveeStrip("6871", verbose=False) as strip:
        print("Connected.")

        await strip.power()
        strip.clear()
        await strip.show()

        colors = [
            "#{:02X}{:02X}{:02X}".format(*(round(channel * 255) for channel in colorsys.hsv_to_rgb(degree / 360, 1, 1)))
            for degree in range(360)
        ]

        for i in range(lights):
            strip.set([i], "#0000FF")
            if animate:
                await strip.show(wait=True)
                # await asyncio.sleep(0.01)

            if i <= len(colors) - 1:
                r, g, b = tuple(int(colors[i][j : j + 2], 16) for j in (1, 3, 5))
            else:
                r, g, b = 0, 0, 0

            strip.set([i], f"#{r:02X}{g:02X}{b:02X}")
        await strip.show(wait=True)


if __name__ == "__main__":
    asyncio.run(main(animate=False))
