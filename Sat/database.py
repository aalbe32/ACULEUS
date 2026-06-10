

"""SQLite storage for pipeline records."""
import logging
import sqlite3
from typing import Optional
 
from config import DB_PATH
from pipeline import PipelineRecord
 
log = logging.getLogger(__name__)
 
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
        received_at         REAL    NOT NULL,
        values_json         TEXT    NOT NULL DEFAULT '{}'
    );
"""
 
READINGS_INDICES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_readings_sensor    ON readings(sensor_name);",
    "CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings(timestamp_rtc);",
    "CREATE INDEX IF NOT EXISTS idx_readings_fault     ON readings(fault);",
]
 
INSERT_SQL = """
    INSERT INTO readings (
        satellite_id, mission_name, sensor_name,
        timestamp_rtc, timestamp_monotonic, schema_version,
        is_anomalous, fault, fault_reason,
        checksum, received_at, values_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""
 
 
class Database:
    """One SQLite connection for the lifetime of the mission."""
 
    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._write_count = 0
        self._error_count = 0
 
    def initialize(self) -> None:
        """Open the connection, apply pragmas, create schema. Call once at startup."""
        try:
            self._conn = sqlite3.connect(self._db_path)
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
 
    def write(self, record: PipelineRecord) -> None:
        """Insert one record. Raises if the DB was never initialized."""
        if self._conn is None:
            raise RuntimeError("Database.write() called before initialize()")
 
        try:
            self._conn.execute(
                INSERT_SQL,
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
                    record.values_json,
                ),
            )
            self._conn.commit()
            self._write_count += 1
        except Exception as e:
            self._error_count += 1
            log.error(f"Failed to write: {e} (total errors: {self._error_count})")
            raise  # let the pipeline count it as a drop
 
    def shutdown(self) -> None:
        """Commit and close. Safe to call even if never initialized."""
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
 