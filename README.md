# YRM100 UHF RFID — read & write

A small command-line tool for reading and writing UHF Gen2 RFID tags with a
YRM100 module on a USB-serial port.

## Install

You need Python 3 and pyserial:

```bash
pip install pyserial
```

Plug the reader in and check your user can talk to it:

```bash
python3 uhf.py ports
```

If that reports a permission error, add yourself to the `dialout` group
(`sudo usermod -aG dialout $USER`, then log out and back in). The tool prints
the same advice when it hits the problem.

## Usage

There are three commands — `read`, `write` and `ports`. The reader's port is
found automatically, so the common cases need no options at all.

```bash
# Read whichever tag is on the antenna
python3 uhf.py read

# Keep scanning until Ctrl-C, with live signal strength and read counts
python3 uhf.py read --continuous

# Give the tag a new EPC (asks for confirmation first)
python3 uhf.py write E28069150000503197B0B1A3

# List connected serial devices
python3 uhf.py ports
```

A successful read looks like:

```
OK  EPC E28068940000502DFB364096   PC 3000   signal ###.. -48 dBm
```

Writing shows the tag's current EPC, asks before overwriting it, and reads the
tag back afterwards so you can see the change took:

```
    Tag found:  EPC E28068940000502DFB364096   PC 3000   signal ###.. -48 dBm
    New EPC:    AAAABBBBCCCCDDDDEEEEFFFF  (96 bits)
    Overwrite the EPC on this tag? [y/N] y
OK  Written and verified:  EPC AAAABBBBCCCCDDDDEEEEFFFF   PC 3000   signal ###.. -48 dBm
```

In `read --continuous`, the display updates in place: one line per tag showing
its current signal strength and how many times it has been read, so you can
watch the signal change as you move a tag around.

### Options

Every command accepts:

| Option | Meaning |
|---|---|
| `-p, --port` | Serial port. Default `auto` — only needed if detection picks wrong. |
| `-b, --baud` | Baud rate, default 115200. |
| `-t, --timeout` | Seconds to wait for a tag, default 3. |
| `--tx-power N` | TX power in 0.01 dBm units, default 2600 (26 dBm, the maximum). |
| `-v, --verbose` | Show the raw serial frames. |
| `--no-color` | Plain output. `NO_COLOR` in the environment does the same. |

`read` adds `--continuous`. `write` adds:

| Option | Meaning |
|---|---|
| `--access-pwd HEX` | Tag access password, 8 hex digits. Default `00000000`. |
| `-y, --yes` | Do not ask before overwriting a tag. |

Exit status is `0` on success, `1` when no tag was found or a write failed,
`2` for a bad argument or no command, `130` on Ctrl-C.

### EPC format

An EPC is hex, and its length must be a multiple of 4 hex digits because tags
store whole 16-bit words. The usual Gen2 EPC is 96 bits = 24 hex digits.
Spaces, colons and dashes in what you type are ignored.

## Hardware

- **Module**: YRM100 (R200 / Impinj E710 based) UHF RFID reader, 860–960 MHz.
- **Connection**: USB-serial, `115200 8N1`.
- **TX power**: 0.01 dBm units, maximum `2600` (26 dBm). Writes need more RF
  headroom than reads, so the tool runs at full power unless told otherwise.

## YRM100 serial protocol (what the tool uses)

Frame layout:

```
0xBB  Type  Cmd  LenHi LenLo  [Params...]  Checksum  0x7E
```

- `Type`: `0x00` command, `0x01` response, `0x02` async notification.
- `Checksum` = `sum(Type..Params) & 0xFF`. Frames that fail the checksum or
  are missing the `0x7E` terminator are dropped.
- `Cmd 0xFF` in a response is an error frame; the first param byte is the code
  (`0x09` access password, `0x10` write failed, `0x15` no tag, `0x16` locked,
  `0xA3` memory overrun).

| Cmd  | Meaning              | Params                                                          |
|------|----------------------|-----------------------------------------------------------------|
| 0x03 | Get module info      | `0x00` hardware, `0x01` software, `0x02` manufacturer           |
| 0x22 | Single inventory     | none                                                            |
| 0x27 | Multi inventory      | `0x22 CountHi CountLo` (`0xFFFF` = continuous)                  |
| 0x28 | Stop multi inventory | none                                                            |
| 0x39 | Read tag memory      | `AccessPwd(4) Bank(1) WordAddr(2) WordCount(2)`                 |
| 0x49 | Write tag memory     | `AccessPwd(4) Bank(1) WordAddr(2) WordCount(2) Data(WordCount*2)` |
| 0xB6 | Set TX power         | `PowHi PowLo` (0.01 dBm units)                                  |

Memory banks: `0x00` reserved (kill/access password), `0x01` EPC, `0x02` TID,
`0x03` user.

EPC bank layout (Gen2 standard, word = 16 bits):

| Word addr | Content                |
|-----------|------------------------|
| 0         | Stored CRC-16          |
| 1         | PC (Protocol Control)  |
| 2..N      | EPC                    |

The high 5 bits of PC hold the EPC length in words, so a 96-bit EPC has
PC = `0x3000`. Writing a same-length EPC starts at word 2 and leaves PC alone;
writing a different length rewrites PC and EPC together from word 1, otherwise
the tag reads back truncated.

An inventory notification (`Type=0x02 Cmd=0x22`) carries
`RSSI(1, signed) PC(2) EPC(N) CRC(2)`. A successful write response
(`Type=0x01 Cmd=0x49`) carries the tag's inventory data plus a trailing status
byte (`0x00` = success).

## Operational notes

- **Writes often fail on the first attempt** while the tag settles in the
  field. The tool retries up to 10 times, but gives up immediately on errors a
  retry cannot fix (wrong password, locked tag, memory overrun).
- **Hold the tag close for writes** — a few centimetres. A tag that reads fine
  at -50 dBm may still be too weak to write.
- The reader keeps streaming if a previous run left it polling, so the tool
  always sends `STOP` at startup to get back to a known state.
- Port auto-detection lists USB-serial devices and, when there is more than
  one, asks each in turn for its firmware version (`0x03`) to find the reader.
- Reading the reserved bank with the default password `00000000` returns error
  `0x09` if the tag has an access password set. That is normal.

## Files

- `uhf.py` — the tool.
- `README.md` — this file.
- `AGENTS.md` — notes for coding agents working on this repo.
