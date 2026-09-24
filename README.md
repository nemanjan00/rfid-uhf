# YRM100 UHF RFID — read & write

Small Python tool for reading and writing UHF Gen2 tags via a YRM100
module on a USB-serial port.

## Hardware

- **Module**: YRM100 (R200 / Impinj E710 based) UHF RFID reader, 860–960 MHz.
- **Connection**: USB-serial, default `/dev/ttyUSB1` at `115200 8N1`.
- **TX power**: 0.01 dBm units, max `2600` (26 dBm). Writes need more
  RF headroom than reads — the script sets max power on every run.

## Dependencies

- Python 3
- `pyserial` (tested with 3.5)

## Usage

```bash
# Single read on default port
python3 read_card.py

# Continuous polling (Ctrl-C to stop, EPCs deduplicated)
python3 read_card.py --continuous

# Custom port / baud / timeout
python3 read_card.py -p /dev/ttyUSB0 -b 115200 -t 10

# Write a new EPC to whichever tag is on the antenna
python3 read_card.py --write-epc E28068940000502DFB364096

# Non-default access password / lower TX power
python3 read_card.py --write-epc DEADBEEF... --access-pwd 12345678 --tx-power 2000
```

EPC must be hex, length a multiple of 4 chars (whole 16-bit words). The
typical Gen2 EPC is 96 bits = 24 hex chars.

Output for a successful read:

```
EPC=E28068940000502DFB364096  PC=3000  RSSI=-32 dBm
```

## YRM100 serial protocol (what the script uses)

Frame layout:

```
0xBB  Type  Cmd  LenHi LenLo  [Params...]  Checksum  0x7E
```

- `Type`: `0x00` command, `0x01` response, `0x02` async notification.
- `Checksum` = `sum(Type..Params) & 0xFF`.
- `Cmd 0xFF` in a response = error frame; first param byte is the
  error code (`0x09` access-password error, `0x10` write fail,
  `0x15` no tag found, `0xA3` memory overrun, etc.).

Commands the script uses:

| Cmd  | Meaning              | Params                                                          |
|------|----------------------|-----------------------------------------------------------------|
| 0x22 | Single inventory     | none                                                            |
| 0x27 | Multi inventory      | `0x22 CountHi CountLo` (`0xFFFF` = continuous)                  |
| 0x28 | Stop multi inventory | none                                                            |
| 0x39 | Read tag memory      | `AccessPwd(4) Bank(1) WordAddr(2) WordCount(2)`                 |
| 0x49 | Write tag memory     | `AccessPwd(4) Bank(1) WordAddr(2) WordCount(2) Data(WordCount*2)` |
| 0xB6 | Set TX power         | `PowHi PowLo` (0.01 dBm units)                                  |

Memory banks: `0x00` reserved (kill/access pwd), `0x01` EPC,
`0x02` TID, `0x03` user.

EPC bank layout (Gen2 standard, word = 16 bits):

| Word addr | Content                |
|-----------|------------------------|
| 0         | Stored CRC-16          |
| 1         | PC (Protocol Control)  |
| 2..N      | EPC                    |

So a 96-bit EPC write = `WordAddr=0x0002`, `WordCount=0x0006`.
The high 5 bits of PC encode EPC length in words; for a 96-bit EPC
PC = `0x3000`, so it doesn't need to be rewritten when the new EPC is
also 96 bits.

A successful inventory notification (`Type=0x02 Cmd=0x22`) carries:

```
RSSI(1, signed) PC(2) EPC(N) CRC(2)
```

A successful write response (`Type=0x01 Cmd=0x49`) carries the tag's
inventory data plus a trailing status byte (`0x00` = success).

## Operational notes

- **Writes often fail on the first attempt** (state/power). The script
  retries up to 10 times — this fixed the "partial write" symptoms
  seen during development.
- **Position the tag close to the antenna** (a few cm) for writes; a
  tag that reads fine at -50 dBm may still be too weak to write.
- The reader stays in continuous-inventory mode across script runs if
  it was started that way and not stopped cleanly. The script always
  sends `STOP` at startup to recover from this.
- Reading the reserved bank with the default password `00000000`
  returns error `0x09` if the tag's actual access password is set —
  this is normal.

## Files

- `read_card.py` — the tool.
- `README.md` — this file.
