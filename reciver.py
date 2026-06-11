"""Ground-station receiver — listens for telemetry, validates CRC,
stores everything to a local SQLite DB, and prints a live stats
summary every STATS_INTERVAL_S seconds."""

import json
import re
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------

def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE — must match the sender's implementation."""
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DB_PATH = "ground.db"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def _safe_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


READINGS_SQL = """
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    sat_id        TEXT,
    mission       TEXT,
    sensor_name   TEXT    NOT NULL,
    ts_rtc        TEXT,
    ts_mono       REAL,
    valid         INTEGER NOT NULL,
    fault_reason  TEXT    NOT NULL DEFAULT '',
    crc_ok        INTEGER NOT NULL,
    raw_json      TEXT    NOT NULL
);
"""

INDICES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_g_sensor ON readings(sensor_name);",
    "CREATE INDEX IF NOT EXISTS idx_g_ts     ON readings(ts_rtc);",
    "CREATE INDEX IF NOT EXISTS idx_g_crc    ON readings(crc_ok);",
]

INSERT_SQL = """
INSERT INTO readings (sat_id, mission, sensor_name, ts_rtc, ts_mono,
                      valid, fault_reason, crc_ok, raw_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class ReceiverDB:
    """SQLite store for received telemetry. Per-sensor values tables
    are created on demand, matching the satellite's schema pattern."""

    def __init__(self, path: str = DB_PATH):
        self._path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._sensor_tables: Dict[str, Tuple[str, List[str]]] = {}

    def open(self) -> None:
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(READINGS_SQL)
        for idx in INDICES_SQL:
            self._conn.execute(idx)
        self._conn.commit()
        print(f"DB ready: {self._path}")

    def close(self) -> None:
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def _ensure_sensor_table(
        self, sensor_name: str, value_keys: List[str]
    ) -> Tuple[str, List[str]]:
        cached = self._sensor_tables.get(sensor_name)
        if cached is not None:
            return cached

        table = f"{_safe_ident(sensor_name.lower())}_values"
        keys = sorted(_safe_ident(k) for k in value_keys)

        cols = ["reading_id INTEGER PRIMARY KEY"]
        cols.extend(f"{k} REAL" for k in keys)
        cols.append("FOREIGN KEY (reading_id) REFERENCES readings(id)")

        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)});"
        )
        self._conn.commit()
        self._sensor_tables[sensor_name] = (table, keys)
        print(f"  + created sensor table: {table} {keys}")
        return table, keys

    def store(self, obj: dict, raw_line: bytes, crc_ok: bool) -> None:
        if self._conn is None:
            return

        cur = self._conn.execute(
            INSERT_SQL,
            (
                obj.get("sat"),
                obj.get("mission"),
                obj.get("sensor", "?"),
                obj.get("ts_rtc"),
                obj.get("ts_mono"),
                int(bool(obj.get("valid", False))),
                obj.get("fault_reason", "") or "",
                int(crc_ok),
                raw_line.decode("utf-8", errors="replace"),
            ),
        )
        reading_id = cur.lastrowid

        values = obj.get("values") or {}
        if crc_ok and obj.get("valid") and values:
            sensor_name = obj.get("sensor")
            if sensor_name:
                table, keys = self._ensure_sensor_table(sensor_name, list(values.keys()))
                cols = "reading_id," + ",".join(keys)
                placeholders = ",".join(["?"] * (len(keys) + 1))
                row = [reading_id] + [float(values.get(k, 0.0)) for k in keys]
                self._conn.execute(
                    f"INSERT INTO {table} ({cols}) VALUES ({placeholders});", row
                )

        self._conn.commit()


# ---------------------------------------------------------------------------
# Live stats
# ---------------------------------------------------------------------------

STATS_INTERVAL_S = 10.0


@dataclass
class SensorStats:
    total: int = 0
    crc_fail_total: int = 0
    fault_total: int = 0
    window_count: int = 0       # reset every print
    last_seen: float = 0.0      # time.monotonic()


class StatsTracker:
    """Tracks per-sensor counters and prints a summary every interval.
    Runs the printer on a background thread so it doesn't block reads."""

    def __init__(self, interval_s: float = STATS_INTERVAL_S):
        self._interval = interval_s
        self._lock = threading.Lock()
        self._sensors: Dict[str, SensorStats] = {}
        self._connected = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="stats", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def set_connected(self, state: bool) -> None:
        with self._lock:
            self._connected = state

    def record(self, sensor: str, crc_ok: bool, valid: bool) -> None:
        with self._lock:
            s = self._sensors.setdefault(sensor, SensorStats())
            s.total += 1
            s.window_count += 1
            s.last_seen = time.monotonic()
            if not crc_ok:
                s.crc_fail_total += 1
            if not valid:
                s.fault_total += 1

    def _loop(self) -> None:
        # Event.wait returns True if set (stop), False on timeout.
        while not self._stop.wait(self._interval):
            self._print()

    def _print(self) -> None:
        now = time.monotonic()
        ts = time.strftime("%H:%M:%S")
        with self._lock:
            conn = "connected" if self._connected else "waiting"
            header = f"─── stats @ {ts} ── conn: {conn} "
            print(header + "─" * max(0, 60 - len(header)))

            if not self._sensors:
                print("  (no packets yet)")
            else:
                print(f"  {'sensor':<12s} {'total':>7s} {'rate':>9s} "
                      f"{'crc_fail':>9s} {'faults':>7s} {'last':>8s}")
                for name, s in sorted(self._sensors.items()):
                    rate = s.window_count / self._interval
                    age = now - s.last_seen
                    age_str = f"{age:.1f}s" if age < 999 else "n/a"
                    print(f"  {name:<12s} {s.total:>7d} "
                          f"{rate:>7.1f}/s {s.crc_fail_total:>9d} "
                          f"{s.fault_total:>7d} {age_str:>8s}")
                    s.window_count = 0
            print("─" * 60)


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

HOST = "0.0.0.0"
PORT = 5005


def main() -> None:
    db = ReceiverDB()
    db.open()
    stats = StatsTracker()
    stats.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    print(f"Receiver listening on {HOST}:{PORT}")

    try:
        while True:
            conn, addr = srv.accept()
            print(f"Connected from {addr}")
            stats.set_connected(True)
            buf = b""
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        print(f"Disconnected from {addr}")
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        handle_line(line, db, stats)
            finally:
                conn.close()
                stats.set_connected(False)
    finally:
        stats.stop()
        db.close()


def handle_line(line: bytes, db: ReceiverDB, stats: StatsTracker) -> None:
    if not line.strip():
        return

    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        print(f"bad JSON: {e}")
        return

    sent_crc = obj.pop("crc16", None)
    if sent_crc is None:
        sensor = obj.get("sensor", "?")
        print(f"  ? no crc — {sensor}")
        db.store(obj, line, crc_ok=False)
        stats.record(sensor, crc_ok=False, valid=bool(obj.get("valid")))
        return

    recomputed = crc16_ccitt(json.dumps(obj, separators=(",", ":")).encode())
    crc_ok = sent_crc == recomputed

    mark = "X" if crc_ok else "Y: CRC"
    print(f"  {mark}  {obj.get('sensor', '?'):10s}  "
          f"{obj.get('ts_rtc', '?')}  "
          f"valid={obj.get('valid')}  "
          f"{obj.get('values', {})}")

    db.store(obj, line, crc_ok)
    stats.record(obj.get("sensor", "?"), crc_ok, bool(obj.get("valid")))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")