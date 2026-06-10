"""Synchronous sensor pipeline.
 
Call process_reading(reading, db) for each SensorReading as it arrives.
The pipeline verifies its checksum, builds a PipelineRecord, writes it,
and returns the record (or None if it was dropped).
"""
import json
import logging
import time
import zlib
from dataclasses import dataclass, field, replace
from typing import Optional
 
from config import SATELLITE_ID, MISSION_NAME
from sensors.base import SensorReading
 
log = logging.getLogger(__name__)
 
 
@dataclass
class PipelineRecord:
    """A verified, serialized reading ready for database insertion."""
    satellite_id: str
    mission_name: str
    sensor_name: str
    timestamp_monotonic: float
    timestamp_rtc: str
    values_json: str
    raw_bytes_hex: str
    checksum: int
    schema_version: int
    is_anomalous: bool
    fault: bool
    fault_reason: str
    received_at: float
    values: dict = field(default_factory=dict)
 
 
@dataclass
class PipelineStats:
    """Running counters. Pass the same instance to every process_reading call."""
    records_processed: int = 0
    records_dropped: int = 0
    checksum_failures: int = 0
    last_record_time: float = 0.0
 
 
def verify_checksum(reading: SensorReading) -> SensorReading:
    """Return the reading unchanged, or a fault reading if checksum is bad."""
    if reading.fault or not reading.raw_bytes:
        return reading
 
    computed = zlib.crc32(reading.raw_bytes) & 0xFFFFFFFF
    if computed == reading.checksum:
        return reading
 
    log.error(
        f"Checksum mismatch for {reading.sensor_name} — "
        f"expected 0x{reading.checksum:08X} got 0x{computed:08X}. "
        f"Recording as fault."
    )
    return replace(
        reading,
        values={},
        raw_bytes=b"",
        checksum=0,
        fault=True,
        fault_reason="checksum mismatch — data corrupt in transit",
        is_anomalous=False,
    )
 
 
def build_record(reading: SensorReading, received_at: float) -> PipelineRecord:
    """Serialize a verified reading into a PipelineRecord."""
    return PipelineRecord(
        satellite_id=SATELLITE_ID,
        mission_name=MISSION_NAME,
        sensor_name=reading.sensor_name,
        timestamp_monotonic=reading.timestamp_monotonic,
        timestamp_rtc=reading.timestamp_rtc,
        values_json=json.dumps(reading.values),
        raw_bytes_hex=reading.raw_bytes.hex(),
        checksum=reading.checksum,
        schema_version=reading.schema_version,
        is_anomalous=reading.is_anomalous,
        fault=reading.fault,
        fault_reason=reading.fault_reason,
        received_at=received_at,
        values=reading.values,
    )
 
 
def process_reading(
    reading: SensorReading,
    sinks: list,
    stats: Optional[PipelineStats] = None,
) -> Optional[PipelineRecord]:
    """
    Verify, serialize, and write a single reading.
 
    Returns the PipelineRecord on success, or None if the reading was dropped
    due to an error. Updates stats in place if provided.
    """
    received_at = time.monotonic()
 
    try:
        verified = verify_checksum(reading)
        if stats and verified.fault and not reading.fault:
            stats.checksum_failures += 1
 
        record = build_record(verified, received_at)
        
        for sink in sinks:
            try:
                sink.write(record)
            except Exception as e:
                log.error(f"Sink {type(sink).__name__} failed: {e}")
 
        if stats:
            stats.records_processed += 1
            stats.last_record_time = received_at
        return record
 
    except Exception as e:
        log.error(f"Pipeline error processing reading from {reading.sensor_name}: {e}")
        if stats:
            stats.records_dropped += 1
        return None