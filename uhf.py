#!/usr/bin/env python3
"""Read and write UHF Gen2 RFID tags with a YRM100 module.

Frame format (YRM100):
  Header(0xBB) Type Cmd ParamLenHi ParamLenLo [Param...] Checksum End(0x7E)
  Checksum = sum(Type..Param) & 0xFF
"""
import argparse
import os
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("This tool needs pyserial.  Install it with:\n\n    pip install pyserial\n")

HEADER = 0xBB
END = 0x7E
CMD_GET_INFO = 0x03
CMD_SINGLE_POLL = 0x22
CMD_MULTI_POLL = 0x27
CMD_STOP_POLL = 0x28
CMD_READ = 0x39
CMD_WRITE = 0x49
CMD_SET_TX_POWER = 0xB6
CMD_ERROR = 0xFF

MEM_RESERVED = 0x00
MEM_EPC = 0x01
MEM_TID = 0x02
MEM_USER = 0x03

MAX_TX_POWER = 2600
WRITE_ATTEMPTS = 10

# Error codes the module returns in a 0xFF frame, in plain language.  The ones
# in PERMANENT_ERRORS will never succeed on a retry, so writing stops at once.
ERRORS = {
    0x09: "wrong access password (the tag is password protected)",
    0x10: "the tag refused the write",
    0x15: "no tag found",
    0x16: "the tag is locked against writing",
    0xA3: "wrote past the end of that memory bank",
    0xB0: "could not read the tag",
}

# USB-serial chips these modules ship with, used to shortlist ports.
PERMANENT_ERRORS = {0x09: "password", 0x16: "locked", 0xA3: "overrun"}

KNOWN_USB_SERIAL = {0x1A86, 0x10C4, 0x0403, 0x067B}


# --- terminal output -------------------------------------------------------

USE_COLOR = False


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def ok(msg):
    print(f"{paint('OK', '32')}  {msg}")


def fail(msg):
    print(f"{paint('!', '31')}   {msg}", file=sys.stderr)


def warn(msg):
    print(f"{paint('!', '33')}   {msg}")


def info(msg):
    print(f"    {msg}")


def hint(msg):
    print(paint(f"    {msg}", "2"))


def wire(direction, frame, verbose):
    if verbose:
        print(paint(f"    {direction} {frame.hex().upper()}", "2"))


# --- protocol --------------------------------------------------------------


def build_frame(cmd, frame_type=0x00, params=b""):
    body = bytes([frame_type, cmd, (len(params) >> 8) & 0xFF, len(params) & 0xFF]) + params
    checksum = sum(body) & 0xFF
    return bytes([HEADER]) + body + bytes([checksum, END])


def read_frame(ser, timeout=1.0, verbose=False):
    """Read one valid frame from serial.  Returns a dict, or None on timeout.

    Frames with a bad checksum or terminator are dropped rather than parsed.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        if b[0] != HEADER:
            continue

        head = ser.read(4)  # type, cmd, lenHi, lenLo
        if len(head) < 4:
            return None
        ftype, cmd, lh, ll = head
        params = ser.read((lh << 8) | ll)
        tail = ser.read(2)  # checksum, end
        if len(tail) < 2:
            return None
        checksum, end = tail
        if checksum != (sum(head) + sum(params)) & 0xFF or end != END:
            if verbose:
                hint("dropped a corrupt frame")
            continue

        wire("<-", bytes([HEADER]) + head + params + tail, verbose)
        return {"type": ftype, "cmd": cmd, "params": params}
    return None


def send(ser, frame, verbose):
    wire("->", frame, verbose)
    ser.write(frame)


def describe_error(params):
    if not params:
        return "the reader reported an error"
    return ERRORS.get(params[0], f"the reader reported error 0x{params[0]:02X}")


def parse_inventory(params):
    """Inventory payload: RSSI(1, signed) PC(2) EPC(N) CRC(2)."""
    if len(params) < 7:
        return None
    rssi = params[0] - 256 if params[0] > 127 else params[0]
    return {
        "rssi_dbm": rssi,
        "pc": int.from_bytes(params[1:3], "big"),
        "epc": params[3:-2].hex().upper(),
    }


def signal_bar(rssi):
    """Five-step signal strength, roughly -30 dBm (strong) to -70 (weak)."""
    filled = max(0, min(5, round((rssi + 75) / 9)))
    return "#" * filled + "." * (5 - filled)


def format_tag(tag):
    return (f"EPC {paint(tag['epc'], '1')}   PC {tag['pc']:04X}   "
            f"signal {signal_bar(tag['rssi_dbm'])} {tag['rssi_dbm']} dBm")


# --- finding the reader ----------------------------------------------------


def candidate_ports():
    """USB-serial ports, most likely reader first.  Built-in ttyS* are skipped."""
    ports = [p for p in serial.tools.list_ports.comports() if p.vid is not None]
    ports.sort(key=lambda p: (p.vid not in KNOWN_USB_SERIAL, p.device))
    return ports


def probe(port, baud, verbose):
    """Ask a port for its firmware version.  True if a YRM100 answers."""
    try:
        with serial.Serial(port, baud, timeout=0.1) as ser:
            time.sleep(0.1)
            ser.reset_input_buffer()
            send(ser, build_frame(CMD_GET_INFO, params=b"\x01"), verbose)
            deadline = time.time() + 0.6
            while time.time() < deadline:
                f = read_frame(ser, timeout=0.3, verbose=verbose)
                if f and f["cmd"] == CMD_GET_INFO:
                    return True
    except (OSError, serial.SerialException):
        pass
    return False


def autodetect_port(baud, verbose):
    """Pick the reader's port, or exit with advice on what to do instead."""
    ports = candidate_ports()
    if not ports:
        fail("No USB-serial device found -- is the reader plugged in?")
        hint("Plug it in, wait a second, and try again.")
        hint("Already plugged in?  Run:  uhf.py ports")
        sys.exit(1)

    if len(ports) == 1:
        info(f"Using {ports[0].device} ({ports[0].description})")
        return ports[0].device

    info(f"Found {len(ports)} serial devices, looking for the reader...")
    for p in ports:
        if probe(p.device, baud, verbose):
            info(f"Using {p.device} ({p.description})")
            return p.device

    fail("Several serial devices are connected and none answered as a YRM100.")
    for p in ports:
        info(f"  {p.device}  {p.description}")
    hint("Pick one by hand, e.g.:  uhf.py read -p " + ports[0].device)
    sys.exit(1)


def open_reader(args):
    """Open the reader, put it in a known state, and return the serial port."""
    port = autodetect_port(args.baud, args.verbose) if args.port == "auto" else args.port
    try:
        ser = serial.Serial(port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        text = str(e)
        fail(f"Could not open {port}: {text}")
        if "Permission denied" in text:
            hint("Your user needs serial access.  On most Linux systems:")
            hint("    sudo usermod -aG dialout $USER     # then log out and back in")
            hint("Or run this once:  sudo chmod a+rw " + port)
        elif "No such file" in text:
            hint("That port does not exist.  See what is available:")
            hint("    uhf.py ports")
        else:
            hint("Another program may be holding the port open.")
        sys.exit(1)

    time.sleep(0.1)
    # The reader keeps streaming between runs if it was left polling.
    send(ser, build_frame(CMD_STOP_POLL), args.verbose)
    time.sleep(0.2)
    ser.reset_input_buffer()
    # Writes need more RF headroom than reads, so run at full power by default.
    send(ser, build_frame(CMD_SET_TX_POWER, params=args.tx_power.to_bytes(2, "big")), args.verbose)
    time.sleep(0.2)
    ser.reset_input_buffer()
    return ser


# --- commands --------------------------------------------------------------


def scan(ser, timeout, verbose, label="Hold a tag near the antenna", settle=0.6):
    """Poll for tags until the field is quiet or the timeout runs out.

    Returns (tags, reader_answered).  `tags` maps EPC to the latest reading,
    so several tags in the field all get reported instead of whichever one
    happened to answer first.  Once a tag replies, polling continues for
    `settle` seconds to give the others a chance to be heard.

    `reader_answered` tells a caller whether the reader itself was talking to
    us, so "no tag" can be told apart from "nothing on the port".
    """
    spin = "|/-\\"
    tick = 0
    deadline = time.time() + timeout
    interactive = sys.stdout.isatty()
    if interactive:
        print(f"    {label}... ", end="", flush=True)

    tags = {}
    answered = False
    quiet_by = None
    last_poll = 0.0
    try:
        while time.time() < deadline and (quiet_by is None or time.time() < quiet_by):
            if time.time() - last_poll > 0.2:
                send(ser, build_frame(CMD_SINGLE_POLL), verbose)
                last_poll = time.time()
            f = read_frame(ser, timeout=0.2, verbose=verbose)
            if interactive:
                print(f"\b{spin[tick % 4]}", end="", flush=True)
                tick += 1
            if not f:
                continue
            answered = True
            if f["cmd"] == CMD_SINGLE_POLL and f["type"] in (0x01, 0x02):
                tag = parse_inventory(f["params"])
                if tag:
                    tags[tag["epc"]] = tag
                    if quiet_by is None or settle == 0:
                        quiet_by = time.time() + settle
    finally:
        if interactive:
            print("\r\033[K", end="", flush=True)
    return tags, answered


def by_signal(tags):
    return sorted(tags.values(), key=lambda t: -t["rssi_dbm"])


def cmd_read(ser, args):
    tags, answered = scan(ser, args.timeout, args.verbose)
    if not tags:
        fail("No tag found." if answered else "The reader is not responding.")
        if not answered:
            hint("Check the port and baud rate, or unplug and replug the reader.")
            hint("See what is connected:  uhf.py ports")
            return 1
        hint("Hold the tag a few centimetres from the antenna and try again.")
        hint("To keep scanning until you stop it:  uhf.py read --continuous")
        return 1

    if len(tags) == 1:
        ok(format_tag(next(iter(tags.values()))))
    else:
        ok(f"{len(tags)} tags in the field, strongest first:")
        for tag in by_signal(tags):
            info(f"  {format_tag(tag)}")
        hint("Writing needs a single tag in the field.")
    return 0


def cmd_continuous(ser, args):
    send(ser, build_frame(CMD_MULTI_POLL, params=bytes([0x22, 0xFF, 0xFF])), args.verbose)
    info("Scanning continuously.  Press Ctrl-C to stop.")
    print()

    live = sys.stdout.isatty() and not args.verbose
    seen = {}      # EPC -> {"tag": latest reading, "count": reads}
    drawn = 0      # lines of live display currently on screen
    last_draw = 0.0

    try:
        while True:
            f = read_frame(ser, timeout=1.0, verbose=args.verbose)
            if f and f["cmd"] == CMD_SINGLE_POLL and f["type"] in (0x01, 0x02):
                tag = parse_inventory(f["params"])
                if tag:
                    entry = seen.setdefault(tag["epc"], {"count": 0})
                    entry["tag"] = tag
                    entry["count"] += 1
                    if not live:
                        if entry["count"] == 1:
                            print(f"    {format_tag(tag)}")
            # Redraw the whole block in place so signal strength tracks the tag
            # as it moves, instead of scrolling one line per read.
            if live and time.time() - last_draw > 0.15:
                drawn = draw_live(seen, drawn)
                last_draw = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        send(ser, build_frame(CMD_STOP_POLL), args.verbose)
        if live:
            draw_live(seen, drawn)

    print()
    if seen:
        total = sum(e["count"] for e in seen.values())
        ok(f"Stopped.  {len(seen)} tag(s) seen, {total} reads.")
        if not live:  # the live table already shows the per-tag breakdown
            for epc, e in sorted(seen.items(), key=lambda kv: -kv[1]["count"]):
                info(f"  {epc}  x{e['count']}")
    else:
        warn("Stopped.  No tags seen.")
    return 0


def draw_live(seen, drawn):
    """Redraw the tag table in place.  Returns the number of lines drawn."""
    if drawn:
        sys.stdout.write(f"\033[{drawn}A")
    lines = [f"    {format_tag(e['tag'])}  x{e['count']}"
             for e in sorted(seen.values(), key=lambda e: -e["count"])]
    for line in lines:
        sys.stdout.write("\033[K" + line + "\n")
    sys.stdout.flush()
    return len(lines)


def parse_epc(text, what="EPC"):
    """Validate hex the user typed.  Returns bytes, or exits with advice."""
    clean = text.replace(" ", "").replace(":", "").replace("-", "").upper()
    if not clean:
        fail(f"The {what} is empty.")
        sys.exit(2)
    bad = sorted(set(clean) - set("0123456789ABCDEF"))
    if bad:
        fail(f"The {what} may only contain hex digits (0-9, A-F).  Found: {' '.join(bad)}")
        sys.exit(2)
    if len(clean) % 4:
        fail(f"The {what} is {len(clean)} hex digits; it must be a multiple of 4.")
        if what == "EPC":
            hint("Tags store whole 16-bit words, so 4 hex digits at a time.")
            hint("A normal 96-bit EPC is 24 digits, e.g. E28068940000502DFB364096")
        sys.exit(2)
    return bytes.fromhex(clean)


def confirm(question):
    if not sys.stdin.isatty():
        return True
    try:
        return input(f"    {question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_write(ser, args):
    epc, pwd = args.epc_bytes, args.pwd_bytes
    words = len(epc) // 2

    found, answered = scan(ser, args.timeout, args.verbose, "Looking for a tag to write")
    if not found:
        fail("No tag found, so there is nothing to write to."
             if answered else "The reader is not responding.")
        if not answered:
            hint("Check the port and baud rate, or unplug and replug the reader.")
            return 1
        hint("Writing needs the tag closer than reading does -- a few centimetres.")
        return 1

    # There is no way to aim a write at one tag, so refuse rather than
    # overwrite whichever one happens to answer first.
    if len(found) > 1:
        fail(f"{len(found)} tags are in the field, so the write would hit an "
             "arbitrary one:")
        for tag in by_signal(found):
            info(f"  {format_tag(tag)}")
        hint("Leave only the tag you want to write near the antenna.")
        return 1

    current = next(iter(found.values()))

    info(f"Tag found:  {format_tag(current)}")
    if current["epc"] == epc.hex().upper():
        ok("That tag already has this EPC.  Nothing to do.")
        return 0

    info(f"New EPC:    {paint(epc.hex().upper(), '1')}  ({len(epc) * 8} bits)")
    if not args.yes and not confirm("Overwrite the EPC on this tag?"):
        info("Cancelled, tag untouched.")
        return 1

    # The high 5 bits of PC hold the EPC length in words; rewrite PC as well
    # when the new EPC is a different length, or the tag reads back truncated.
    new_pc = (words << 11) | (current["pc"] & 0x07FF)
    if new_pc == current["pc"]:
        addr, data = 2, epc
    else:
        info(f"EPC length changed, so PC {current['pc']:04X} -> {new_pc:04X} too.")
        addr, data = 1, new_pc.to_bytes(2, "big") + epc

    params = (pwd + bytes([MEM_EPC]) + addr.to_bytes(2, "big")
              + (len(data) // 2).to_bytes(2, "big") + data)
    frame = build_frame(CMD_WRITE, params=params)

    # The first attempt often fails while the tag settles in the field.
    last_error = "no response from the reader"
    attempts = 0
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        attempts = attempt
        ser.reset_input_buffer()
        send(ser, frame, args.verbose)
        deadline = time.time() + 1.5
        while time.time() < deadline:
            f = read_frame(ser, timeout=0.5, verbose=args.verbose)
            if not f or f["cmd"] not in (CMD_WRITE, CMD_ERROR):
                continue
            if f["cmd"] == CMD_WRITE and f["type"] == 0x01:
                return verify_write(ser, args, epc)
            last_error = describe_error(f["params"])
            if f["params"] and f["params"][0] in PERMANENT_ERRORS:
                return report_write_failure(last_error, attempts)  # retrying cannot help
            break
        if attempt >= WRITE_ATTEMPTS:
            break
        if attempt == 1:
            info("Retrying...")
        time.sleep(0.1)

    return report_write_failure(last_error, attempts)


def report_write_failure(last_error, attempts):
    tried = f" after {attempts} attempts" if attempts > 1 else ""
    fail(f"Write failed{tried}: {last_error}.")
    if "password" in last_error:
        hint("Pass the tag's password with --access-pwd <8 hex digits>.")
    elif "locked" in last_error:
        hint("This tag's EPC is locked and cannot be changed.")
    else:
        hint("Move the tag closer to the antenna and try again.")
    return 1


def verify_write(ser, args, epc):
    """Read the tag back so the user sees the change, not just a success line."""
    time.sleep(0.2)
    tags, _ = scan(ser, 2.0, args.verbose, "Checking the tag")
    written = tags.get(epc.hex().upper())
    if written:
        ok(f"Written and verified:  {format_tag(written)}")
        return 0
    if tags:
        others = ", ".join(sorted(tags))
        fail(f"The reader accepted the write, but the tag reads back as {others}.")
        hint("Try again with the tag held closer to the antenna.")
        return 1
    warn("The reader accepted the write, but the tag did not answer the read-back.")
    hint("Check it with:  uhf.py read")
    return 0


def cmd_ports(args):
    ports = candidate_ports()
    if not ports:
        fail("No USB-serial devices found.")
        hint("Plug the reader in and run this again.")
        return 1
    info("USB-serial devices:")
    for p in ports:
        info(f"  {p.device:<16} {p.description}")
    print()
    hint("The reader is picked automatically when only one is connected.")
    hint(f"Otherwise choose one:  uhf.py read -p {ports[0].device}")
    return 0


# --- entry point -----------------------------------------------------------

EXAMPLES = """\
examples:
  uhf.py read                          read the tag on the antenna
  uhf.py read --continuous             keep scanning until Ctrl-C
  uhf.py write E28069150000503197B0B1A3    give the tag a new EPC
  uhf.py ports                         show connected serial devices

The reader's port is found automatically; use -p only if that picks wrong.
"""


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--port", default="auto",
                        help="serial port, e.g. /dev/ttyUSB0 (default: find it automatically)")
    common.add_argument("-b", "--baud", type=int, default=115200,
                        help="baud rate (default: 115200)")
    common.add_argument("-t", "--timeout", type=float, default=3.0,
                        help="seconds to wait for a tag (default: 3)")
    common.add_argument("--tx-power", type=int, default=MAX_TX_POWER, metavar="N",
                        help=f"TX power in 0.01 dBm units (default: {MAX_TX_POWER} = 26 dBm)")
    common.add_argument("--verbose", "-v", action="store_true",
                        help="show the raw serial frames")
    common.add_argument("--no-color", action="store_true", help="disable coloured output")

    ap = argparse.ArgumentParser(
        prog="uhf.py",
        description="Read and write UHF Gen2 RFID tags with a YRM100 reader.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = ap.add_subparsers(dest="command", metavar="COMMAND")

    p_read = subs.add_parser("read", parents=[common], help="read the tag on the antenna",
                             description="Read the tag on the antenna.")
    p_read.add_argument("--continuous", action="store_true", help="keep scanning until Ctrl-C")

    p_write = subs.add_parser("write", parents=[common], help="write a new EPC to the tag",
                              description="Write a new EPC to the tag on the antenna.")
    p_write.add_argument("epc", metavar="EPC",
                         help="the new EPC as hex (24 digits is the usual 96-bit EPC)")
    p_write.add_argument("--access-pwd", default="00000000", metavar="HEX",
                         help="tag access password, 8 hex digits (default: 00000000)")
    p_write.add_argument("--yes", "-y", action="store_true",
                         help="do not ask before overwriting the tag")

    subs.add_parser("ports", parents=[common], help="list connected serial devices",
                    description="List connected USB-serial devices.")
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()

    if not args.command:
        ap.print_help()
        return 2

    global USE_COLOR
    USE_COLOR = sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR")

    # Check everything the user typed before going near the hardware, so a
    # typo is reported straight away instead of after a reader hunt.
    if not 0 < args.tx_power <= MAX_TX_POWER:
        fail(f"--tx-power must be between 1 and {MAX_TX_POWER} (0.01 dBm units).")
        return 2
    if args.command == "write":
        args.epc_bytes = parse_epc(args.epc, "EPC")
        args.pwd_bytes = parse_epc(args.access_pwd, "access password")
        if len(args.pwd_bytes) != 4:
            fail("The access password must be 8 hex digits (4 bytes).")
            return 2

    if args.command == "ports":
        return cmd_ports(args)

    ser = open_reader(args)
    try:
        if args.command == "write":
            return cmd_write(ser, args)
        if args.continuous:
            return cmd_continuous(ser, args)
        return cmd_read(ser, args)
    finally:
        ser.close()


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
