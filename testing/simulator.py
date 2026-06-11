"""Telemetry simulator — pretends to be the satellite for testing the receiver.

Sends realistic-looking AS7331 packets to a TCP host:port at a configurable rate.
Optionally injects faults and bad-CRC packets so you can verify the receiver
handles them correctly.

Usage:
    python simulator.py                          # localhost:5005, 1 Hz, forever
    python simulator.py --rate 5                 # 5 packets/sec
    python simulator.py --fault-rate 0.1         # 10% of packets are faults
    python simulator.py --bad-crc-rate 0.05      # 5% of packets have corrupt CRC
    python simulator.py --host 192.168.1.50 --port 5005
    python simulator.py --duration 30            # stop after 30 seconds

Ctrl-C to stop early.
"""
import argparse
import json
import random
import socket
import time
from datetime import datetime, timezone


# --------------------------------------------------------------------------
# CRC — must match the satellite's telemetry.py exactly
# --------------------------------------------------------------------------

def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


# --------------------------------------------------------------------------
# Fake AS7331 reading generator
# --------------------------------------------------------------------------

def fake_as7331_values() -> dict:
    """Plausible indoor readings with small per-cycle jitter."""
    return {
        "uva_uw_cm2":    round(random.uniform(2300, 2500), 4),
        "uvb_uw_cm2":    round(random.uniform(2700, 2900), 4),
        "uvc_uw_cm2":    round(random.uniform(1400, 1550), 4),
        "temperature_c": round(random.uniform(24.5, 26.5), 2),
    }


def build_packet(sensor: str, fault: bool, corrupt_crc: bool) -> bytes:
    """Build one newline-terminated telemetry line."""
    body = {
        "version":      1,
        "sat":          "ACULEUS-1",
        "mission":      "ACULEUS",
        "sensor":       sensor,
        "ts_rtc":       datetime.now(timezone.utc).isoformat(),
        "ts_mono":      time.monotonic(),
        "valid":        not fault,
        "fault_reason": "simulated fault" if fault else "",
        "values":       {} if fault else fake_as7331_values(),
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    crc = crc16_ccitt(raw)
    if corrupt_crc:
        crc ^= 0xFFFF  # any deterministic flip works
    body["crc16"] = crc
    return (json.dumps(body, separators=(",", ":")) + "\n").encode("utf-8")


# --------------------------------------------------------------------------
# Sender
# --------------------------------------------------------------------------

def connect(host: str, port: int) -> socket.socket:
    """Connect with retries — receiver might not be up yet."""
    while True:
        try:
            s = socket.create_connection((host, port), timeout=2.0)
            print(f"connected to {host}:{port}")
            return s
        except OSError as e:
            print(f"connect failed ({e}) — retrying in 2s")
            time.sleep(2.0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5005)
    p.add_argument("--rate", type=float, default=1.0,
                   help="packets per second (default 1.0)")
    p.add_argument("--fault-rate", type=float, default=0.0,
                   help="fraction of packets that are faults (0.0-1.0)")
    p.add_argument("--bad-crc-rate", type=float, default=0.0,
                   help="fraction of packets with corrupt CRC (0.0-1.0)")
    p.add_argument("--sensor", default="AS7331",
                   help="sensor name to put in packets")
    p.add_argument("--duration", type=float, default=None,
                   help="seconds to run (default: forever)")
    args = p.parse_args()

    interval = 1.0 / args.rate
    sock = connect(args.host, args.port)

    sent = 0
    faults = 0
    bad_crcs = 0
    start = time.monotonic()

    print(f"sending {args.rate} pkt/s "
          f"(faults={args.fault_rate:.0%}, bad_crc={args.bad_crc_rate:.0%})")

    try:
        while True:
            if args.duration and (time.monotonic() - start) >= args.duration:
                break

            fault = random.random() < args.fault_rate
            corrupt = random.random() < args.bad_crc_rate
            packet = build_packet(args.sensor, fault, corrupt)

            try:
                sock.sendall(packet)
                sent += 1
                if fault:
                    faults += 1
                if corrupt:
                    bad_crcs += 1
                tag = ("FAULT " if fault else "ok    ") + ("BAD_CRC" if corrupt else "")
                print(f"  → #{sent:04d} {tag}")
            except OSError as e:
                print(f"send failed ({e}) — reconnecting")
                try:
                    sock.close()
                except OSError:
                    pass
                sock = connect(args.host, args.port)
                continue

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        try:
            sock.close()
        except OSError:
            pass
        elapsed = time.monotonic() - start
        print(f"\nsent={sent} faults={faults} bad_crcs={bad_crcs} "
              f"elapsed={elapsed:.1f}s")


if __name__ == "__main__":
    main()
