# Govee H6871 Decryptor (partly reverse-engineered)

Decrypts and labels Bluetooth traffic between the Govee Home app and a Govee light H6871 from an Apple PacketLogger capture (`.pklg`).

## Requirements

```
pip install cryptography
```

## Usage

```
python govee_pklg_decode.py capture.pklg
python govee_pklg_decode.py capture.pklg --no-keepalive   # hide keepalive (aa) packets
python govee_pklg_decode.py capture.pklg --only a4 33      # show only packets starting with a4 or 33
```

## Recording a capture

1. Quit the Govee app completely.
2. Start a new iOS trace in PacketLogger.
3. Open the Govee app so the connection and handshake are recorded.
4. Perform the actions you want to analyze, with a few seconds between each.
5. Save the trace as `.pklg`.

The handshake must be in the capture. Without it the session key is unknown and packets are shown as `?? cannot decrypt`.

## What it does

### Reading the capture

- Parses the PacketLogger file format and extracts ATT packets.
- Reassembles fragmented packets (DIY packets are often longer than one radio packet).
- Detects disconnects and resets the session key for each new connection.
- Finds the Govee write (`…2b11`) and notify (`…2b10`) characteristics during service discovery.

### Decryption

- Decrypts the handshake with the fixed Govee key `MakingLifeSmarte`.
- Extracts the session key from the light's handshake reply and prints it.
- Decrypts all following packets: AES-128-ECB for each full 16-byte block, RC4 for the remaining bytes.
- Verifies every packet with its XOR checksum, so wrongly decrypted packets are never shown as valid.

### Labels

Each packet is printed with time, direction (`iPhone → Strip` or `Strip → iPhone`), decrypted bytes and a description.

| Packet | Label | Status |
|---|---|---|
| `e7 01` / `e7 02` | Handshake 1 / 2, reply marked | confirmed |
| `aa …` | Keepalive | confirmed |
| `ab …` | Device info request / reply | confirmed |
| `ee …` | Status from strip | confirmed |
| `33 05 15 01 RR GG BB …` | Whole-strip color, with RGB and mask | confirmed |
| `a4 00 00 …` | DIY drawing, part 1 | confirmed |
| `a4 ff ff …` | DIY last part; drawings over 505 bytes continue here, otherwise empty. The parts are joined and the drawing is fully decoded here (see below) | confirmed |
| `33 05 0a 20 03 03` | DIY apply (switch to DIY mode) | inferred |
| `a4 03` from strip | DIY acknowledgement | confirmed |
| `33 xx` from strip | Acknowledgement | confirmed |
| `33 01 01` / `33 01 00` | Power ON / OFF | common Govee format, not yet confirmed |
| `33 04 xx` | Brightness | common Govee format, not yet confirmed |
| `33 09 …` | Init on connect | meaning unknown |
| anything else | `(unknown)` | – |

### DIY packet decoding

For each drawing (on its `a4 ff ff` line) the script shows:

- **Effect** (byte 7) with name: `14` Breathe, `0f` Twinkle, `13` Fade
- **Speed** (byte 8) in percent
- **Brightness** (byte 9) in percent
- **Background color** (bytes 10–12), `010101` shown as "off"
- **LED groups**: color, number of LEDs, and LED indices compressed into ranges, e.g. `86-87,92-93,95`
- Warnings for unreadable groups or unexpected trailing bytes

Example output:

```
DIY | effect=13 (Fade) speed=41% brightness=100% background=ff921d
      Group 1: color ff0000, 50 LEDs: 86-87,92-93,95,97-100,112-114,...,318-328
```

## Extending

New effect codes can be added to the `EFFECTS` dictionary at the top of the labels section. Unknown commands appear as `(unknown)`.
