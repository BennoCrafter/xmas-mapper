import asyncio

from govee_strip import GoveeStrip


async def main():
    async with GoveeStrip("6871", verbose=False) as strip:
        print("Connected.")
        await strip.power()
        strip.clear()
        await strip.show()

        for i in range(375):
            strip.set([i], "#0000FF")
            await strip.show(wait=True)


if __name__ == "__main__":
    asyncio.run(main())
