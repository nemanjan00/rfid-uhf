# Notes for coding agents

Context an agent needs before changing this repo. `README.md` is the user-facing
doc; this file is about how the code is put together and how to test it.

## What this is

One file, `uhf.py`, driving a YRM100 UHF RFID reader over USB-serial. No
package, no build step, no test framework. Python 3 + `pyserial`, nothing else.
Keep it that way unless there is a real reason not to — the tool's value is that
it runs anywhere with a copy and a pip install.

## Layout of `uhf.py`

Sections are marked with `# --- ... ---` comments, in this order:

1. **terminal output** — `paint`/`ok`/`fail`/`warn`/`info`/`hint`/`wire`. All
   user-facing text goes through these so colour and indentation stay uniform.
2. **protocol** — `build_frame`, `read_frame`, `send`, `parse_inventory`.
   `read_frame` verifies the checksum and terminator and drops bad frames.
3. **finding the reader** — `candidate_ports`, `probe`, `autodetect_port`,
   `open_reader`.
4. **commands** — `scan`, `cmd_read`, `cmd_continuous`, `cmd_write`,
   `verify_write`, `cmd_ports`.
5. **entry point** — `build_parser`, `main`.

## Conventions that matter

- **Errors explain the fix.** Every failure path prints what went wrong and
  then a `hint(...)` with the command or action that resolves it. Do not add a
  failure path that leaves the user with only a status code or a raw exception.
- **Validate before opening the port.** `main` checks arguments up front so a
  typo is caught instantly rather than after the reader hunt.
- **Writes are confirmed and verified.** `cmd_write` scans first to show what is
  about to be overwritten, confirms unless `--yes`, then re-reads the tag so the
  user sees the result rather than a bare "OK".
- **Retries only where retrying helps.** Write retries exist because the first
  attempt often fails while the tag settles in the field. Codes in
  `PERMANENT_ERRORS` (wrong password, locked, overrun) stop immediately.
- **Raw frames live behind `-v`.** Default output is for someone who does not
  know the protocol.

## Protocol gotchas

- The high 5 bits of the PC word hold the EPC length in words. Writing an EPC of
  a different length **must** rewrite PC too, from word 1, or the tag reads back
  truncated. `cmd_write` handles this; do not "simplify" it away.
- The reader keeps streaming if a previous run left it in multi-poll, so
  `open_reader` always sends `STOP` first.
- Full command and error-code tables are in `README.md`.

## Testing without hardware

There is no test suite. A pty-based simulator is the practical way to exercise
the code paths — open a pty with `os.openpty()`, answer `0x22` with an inventory
frame and `0x49` by updating the simulated tag, and run `uhf.py -p <pty>`
against it. That covers read, continuous, write, the PC-rewrite path and the
error paths, none of which need a real tag.

What a simulator cannot tell you: real RSSI behaviour, writes that fail from
weak coupling, and the live `--continuous` display (it only activates on a tty —
run it under `script -qc` to see it).

## Hardware notes

- The reader has shown up as `/dev/ttyUSB0`; do not hard-code it, port
  auto-detection exists for exactly this reason.
- Writes need the tag closer to the antenna than reads do.
- Error `0x15` ("no tag found") coming back steadily means the reader is
  healthy and there is simply no tag in the field. `scan` distinguishes this
  from a silent port, and the two get different messages.
