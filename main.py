"""Entry point for all tools: python main.py <tool> [args...]"""

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))  # light tests import govee_strip

TOOLS = {
    "leds": ("src/govee_strip.py", "control the Govee H6871 (--help for options)"),
    "decode": ("scripts/pklg_decode.py", "decode a PacketLogger .pklg capture"),
    "led-ids": ("scripts/led_id_codec.py", "demo of LED id color encoding"),
    "test-sweep": ("light_tests/blue_sweep.py", "light test: blue LED sweep"),
    "test-rainbow": ("light_tests/rainbow.py", "light test: rainbow drawing"),
}


def usage():
    print("usage: python main.py <tool> [args...]\n\ntools:")
    for name, (_, help_text) in TOOLS.items():
        print(f"  {name:<14} {help_text}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in TOOLS:
        usage()
        sys.exit(0 if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help") else 1)
    path = ROOT / TOOLS[sys.argv[1]][0]
    sys.argv = [str(path), *sys.argv[2:]]
    runpy.run_path(str(path), run_name="__main__")
