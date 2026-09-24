#!/usr/bin/env python3
"""Read UHF RFID tag(s) from YRM100 module on a serial port.

Frame format (YRM100):
  Header(0xBB) Type Cmd ParamLenHi ParamLenLo [Param...] Checksum End(0x7E)
  Checksum = sum(Type..Param) & 0xFF
"""
import argparse
import sys
import time
import serial

HEADER = 0xBB
END = 0x7E
CMD_SINGLE_POLL = 0x22
CMD_MULTI_POLL = 0x27
CMD_STOP_POLL = 0x28
CMD_READ = 0x39
CMD_WRITE = 0x49
CMD_SET_TX_POWER = 0xB6

MEM_RESERVED = 0x00
MEM_EPC = 0x01
MEM_TID = 0x02
MEM_USER = 0x03


def build_frame(cmd, frame_type=0x00, params=b""):
    body = bytes([frame_type, cmd, (len(params) >> 8) & 0xFF, len(params) & 0xFF]) + params
    checksum = sum(body) & 0xFF
    return bytes([HEADER]) + body + bytes([checksum, END])


def read_frame(ser, timeout=1.0):
    """Read one full frame from serial. Returns dict or None on timeout."""
    deadline = time.time() + timeout
    # Sync on header
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        if b[0] == HEADER:
            break
    else:
        return None

    header_rest = ser.read(4)  # type, cmd, lenHi, lenLo
    if len(header_rest) < 4:
        return None
    ftype, cmd, lh, ll = header_rest
    plen = (lh << 8) | ll
    params = ser.read(plen)
    tail = ser.read(2)  # checksum, end
    if len(tail) < 2:
        return None
    checksum, end = tail
    expected = (sum(header_rest) + sum(params)) & 0xFF
    return {
        "type": ftype,
        "cmd": cmd,
        "params": params,
        "checksum_ok": checksum == expected,
        "end_ok": end == END,
    }


def parse_inventory(params):
    """Single-poll/notify payload: RSSI(1) PC(2) EPC(N) CRC(2). N = len-5."""
    if len(params) < 7:
        return None
    rssi = params[0] - 256 if params[0] > 127 else params[0]
    pc = params[1:3]
    crc = params[-2:]
    epc = params[3:-2]
    return {"rssi_dbm": rssi, "pc": pc.hex().upper(), "epc": epc.hex().upper(), "crc": crc.hex().upper()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", default="/dev/ttyUSB1")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-t", "--timeout", type=float, default=3.0, help="seconds to wait for a tag")
    ap.add_argument("--continuous", action="store_true", help="multi-poll until Ctrl-C")
    ap.add_argument("--write-epc", metavar="HEX",
                    help="write the given EPC (hex, multiple of 4 chars) to the tag on the antenna")
    ap.add_argument("--access-pwd", default="00000000", help="4-byte access password as 8 hex chars")
    ap.add_argument("--tx-power", type=int, default=2600,
                    help="TX power in 0.01 dBm units (2600 = 26 dBm, max for YRM100)")
    args = ap.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=0.1)
    time.sleep(0.1)
    # Make sure reader isn't still streaming from a previous session.
    ser.write(build_frame(CMD_STOP_POLL))
    time.sleep(0.2)
    ser.reset_input_buffer()
    # Set TX power (writes need more headroom than reads).
    ser.write(build_frame(CMD_SET_TX_POWER, params=args.tx_power.to_bytes(2, "big")))
    time.sleep(0.2)
    ser.reset_input_buffer()

    try:
        if args.write_epc is not None:
            epc = bytes.fromhex(args.write_epc)
            if len(epc) % 2 != 0:
                print("EPC hex must be a multiple of 4 chars (whole 16-bit words).")
                return 2
            pwd = bytes.fromhex(args.access_pwd)
            if len(pwd) != 4:
                print("--access-pwd must be 8 hex chars (4 bytes).")
                return 2
            word_count = len(epc) // 2
            params = pwd + bytes([MEM_EPC]) + b"\x00\x02" + word_count.to_bytes(2, "big") + epc
            frame = build_frame(CMD_WRITE, params=params)
            print(f"-> {frame.hex().upper()}  (write EPC={epc.hex().upper()})")
            for attempt in range(1, 11):
                ser.reset_input_buffer()
                ser.write(frame)
                got = None
                deadline = time.time() + 1.5
                while time.time() < deadline:
                    f = read_frame(ser, timeout=0.5)
                    if not f:
                        continue
                    if f["cmd"] in (CMD_WRITE, 0xFF):
                        got = f
                        break
                if got is None:
                    print(f"attempt {attempt}: no response")
                    continue
                print(f"attempt {attempt}: type={got['type']:02X} cmd={got['cmd']:02X} params={got['params'].hex().upper()}")
                if got["cmd"] == CMD_WRITE and got["type"] == 0x01:
                    print("write OK")
                    return 0
                time.sleep(0.1)
            print("write FAILED after retries.")
            return 1
        if args.continuous:
            # Multi poll: param = 0x22 [count_hi count_lo] where count=0xFFFF means continuous
            frame = build_frame(CMD_MULTI_POLL, params=bytes([0x22, 0xFF, 0xFF]))
            ser.write(frame)
            print(f"-> {frame.hex().upper()}  (multi-poll)")
            seen = set()
            try:
                while True:
                    f = read_frame(ser, timeout=1.0)
                    if not f:
                        continue
                    if f["cmd"] == CMD_SINGLE_POLL and f["type"] in (0x01, 0x02):
                        tag = parse_inventory(f["params"])
                        if tag and tag["epc"] not in seen:
                            seen.add(tag["epc"])
                            print(f"EPC={tag['epc']}  PC={tag['pc']}  RSSI={tag['rssi_dbm']} dBm")
            except KeyboardInterrupt:
                ser.write(build_frame(CMD_STOP_POLL))
                print("\nstopped.")
        else:
            frame = build_frame(CMD_SINGLE_POLL)
            ser.write(frame)
            print(f"-> {frame.hex().upper()}  (single-poll)")
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                f = read_frame(ser, timeout=0.5)
                if not f:
                    continue
                if f["cmd"] == CMD_SINGLE_POLL and f["type"] in (0x01, 0x02):
                    tag = parse_inventory(f["params"])
                    if tag:
                        print(f"EPC={tag['epc']}  PC={tag['pc']}  RSSI={tag['rssi_dbm']} dBm")
                        return 0
                elif f["cmd"] == 0xFF:
                    # error frame, e.g. 0x15 = no tag found
                    print(f"error frame: params={f['params'].hex().upper()} (0x15 = no tag found)")
            print("no tag detected within timeout.")
            return 1
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
