import asyncio

from govee_strip import GoveeStrip


async def main():
    print("Connecting...")
    async with GoveeStrip("6871", verbose=False) as strip:
        print("Connected.")

        await strip.power()
        strip.clear()
        await strip.show()
        colors: list[str] = [
            "#FF0000",  # red
            "#00FF00",  # green
            "#0000FF",  # blue
            "#FFFF00",  # yellow
            "#FF00FF",  # magenta
            "#00FFFF",  # cyan
            "#FFFFFF",  # white
        ]

        strip.set_brightness(30)
        for c in colors:
            await strip.fill(c)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
