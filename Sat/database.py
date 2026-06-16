"""SQLite storage for pipeline records.
 
Schema:
    readings(id, satellite_id, mission_name, sensor_name, ...)
        One row per pipeline record, success or fault.
 
    {sensor_name}_values(reading_id, <one REAL column per value key>)
        One row per successful reading, joined to readings on reading_id.
        Fault readings have no row here.
 
The values table for each sensor is created on first sight, using the
keys from the sensor's first non-fault reading. The schema is then
fixed — keys appearing later that weren't in the first reading get
dropped with a warning. Sensors should always emit the same keys.
"""
import logging
import re
import sqlite3
import threading
from typing import Dict, List, Optional, Tuple
 
from config import DB_PATH
from pipeline import PipelineRecord
 
log = logging.getLogger(__name__)
 
# Only safe identifiers can become table/column names — prevents SQL injection
# via sensor names or value keys.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
 
 
def _safe_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name
 
 
PRAGMAS = [
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA cache_size=-8000;",
    "PRAGMA temp_store=MEMORY;",
]
 
READINGS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS readings (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        satellite_id        TEXT    NOT NULL,
        mission_name        TEXT    NOT NULL,
        sensor_name         TEXT    NOT NULL,
        timestamp_rtc       TEXT    NOT NULL,
        timestamp_monotonic REAL    NOT NULL,
        schema_version      INTEGER NOT NULL,
        is_anomalous        INTEGER NOT NULL DEFAULT 0,
        fault               INTEGER NOT NULL DEFAULT 0,
        fault_reason        TEXT    NOT NULL DEFAULT '',
        checksum            INTEGER NOT NULL,
        received_at         REAL    NOT NULL
    );
"""
 
READINGS_INDICES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_readings_sensor    ON readings(sensor_name);",
    "CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings(timestamp_rtc);",
    "CREATE INDEX IF NOT EXISTS idx_readings_fault     ON readings(fault);",
]
 
INSERT_READING_SQL = """
    INSERT INTO readings (
        satellite_id, mission_name, sensor_name,
        timestamp_rtc, timestamp_monotonic, schema_version,
        is_anomalous, fault, fault_reason, checksum, received_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""
 
 
class Database:
    """One SQLite connection for the lifetime of the mission."""
 
    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        # sensor_name -> (table_name, sorted list of column names)
        self._sensor_tables: Dict[str, Tuple[str, List[str]]] = {}
        self._write_count = 0
        self._error_count = 0
        self._lock = threading.Lock()
 
    def initialize(self) -> None:
        """Open the connection, apply pragmas, create the readings table."""
        try:
            with self._lock:
                self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
    
                for pragma in PRAGMAS:
                    self._conn.execute(pragma)
    
                self._conn.execute(READINGS_TABLE_SQL)
                for idx_sql in READINGS_INDICES_SQL:
                    self._conn.execute(idx_sql)
                self._conn.commit()
    
                log.info(f"Database initialized: {self._db_path}")
        except Exception as e:
            log.critical(f"Failed to initialize DB: {e}")
            raise
 
    def _ensure_sensor_table(
        self, sensor_name: str, value_keys: List[str]
    ) -> Tuple[str, List[str]]:
        """
        Create the sensor's values table on first use, return (table, sorted_keys).
        Cached after the first call.
        """
        cached = self._sensor_tables.get(sensor_name)
        if cached is not None:
            return cached
 
        table = f"{_safe_ident(sensor_name.lower())}_values"
        keys = sorted(_safe_ident(k) for k in value_keys)
 
        cols = ["reading_id INTEGER PRIMARY KEY"]
        cols.extend(f"{k} REAL" for k in keys)
        cols.append("FOREIGN KEY (reading_id) REFERENCES readings(id)")
 
        self._conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)});")
        self._conn.commit()
        log.info(f"Ensured sensor table: {table} columns={keys}")
 
        self._sensor_tables[sensor_name] = (table, keys)
        return table, keys
 
    def write(self, record: PipelineRecord) -> None:
        """Insert one record. Fault readings only land in `readings`; non-fault
        readings also get a row in their sensor's values table."""
        with self._lock:   

            if self._conn is None:
                raise RuntimeError("Database.write() called before initialize()")
    
            try:
                cur = self._conn.execute(
                    INSERT_READING_SQL,
                    (
                        record.satellite_id,
                        record.mission_name,
                        record.sensor_name,
                        record.timestamp_rtc,
                        record.timestamp_monotonic,
                        record.schema_version,
                        int(record.is_anomalous),
                        int(record.fault),
                        record.fault_reason,
                        record.checksum,
                        record.received_at,
                    ),
                )
                reading_id = cur.lastrowid
    
                if not record.fault and record.values:
                    table, keys = self._ensure_sensor_table(
                        record.sensor_name, list(record.values.keys())
                    )
    
                    # Drop any keys not in the locked schema, warn once per surprise key
                    extra = set(record.values.keys()) - set(keys)
                    if extra:
                        log.warning(
                            f"{record.sensor_name}: dropping unknown keys {extra} "
                            f"(table schema locked to {keys})"
                        )
    
                    cols = "reading_id," + ",".join(keys)
                    placeholders = ",".join(["?"] * (len(keys) + 1))
                    vals = [reading_id] + [
                        float(record.values.get(k, 0.0)) for k in keys
                    ]
                    self._conn.execute(
                        f"INSERT INTO {table} ({cols}) VALUES ({placeholders});", vals
                    )
    
                self._conn.commit()
                self._write_count += 1
            except Exception as e:
                self._error_count += 1
                log.error(f"Failed to write: {e} (total errors: {self._error_count})")
                raise
 
    def shutdown(self) -> None:
        """Commit and close. Safe to call even if never initialized."""
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.commit()
                self._conn.close()
            finally:
                self._conn = None
            log.info(
                f"Database closed — writes={self._write_count} errors={self._error_count}"
            )