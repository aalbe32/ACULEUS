"""Base classes shared by all sensor drivers."""
import json
import logging
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import SensorConfig, SCHEMA_VERSION

log = logging.getLogger(__name__)


@dataclass
class SensorReading:
    """One reading from one sensor, as handed to the pipeline."""
    sensor_name: str
    timestamp_monotonic: float
    timestamp_rtc: str
    values: dict = field(default_factory=dict)
    raw_bytes: bytes = b""
    checksum: int = 0
    schema_version: int = SCHEMA_VERSION
    is_anomalous: bool = False
    fault: bool = False
    fault_reason: str = ""


class Sensor:
    """
    Base class for all sensor drivers.

    Subclasses implement:
        initialise() -> bool   open the device, return False on failure
        read() -> SensorReading
    """

    def __init__(self, config: SensorConfig):
        self.config = config
        self.name = config.name

    def initialise(self) -> bool:
        raise NotImplementedError

    def read(self) -> SensorReading:
        raise NotImplementedError

    # Helpers for subclasses

    def make_reading(self, values: dict, is_anomalous: bool = False) -> SensorReading:
        """Build a normal reading: serialize values, compute checksum."""
        raw = json.dumps(values, sort_keys=True).encode("utf-8")
        return SensorReading(
            sensor_name=self.name,
            timestamp_monotonic=time.monotonic(),
            timestamp_rtc=datetime.now(timezone.utc).isoformat(),
            values=values,
            raw_bytes=raw,
            checksum=zlib.crc32(raw) & 0xFFFFFFFF,
            is_anomalous=is_anomalous,
        )

    def make_fault(self, reason: str) -> SensorReading:
        """Build a fault reading when the device could not be read."""
        log.warning(f"{self.name} fault: {reason}")
        return SensorReading(
            sensor_name=self.name,
            timestamp_monotonic=time.monotonic(),
            timestamp_rtc=datetime.now(timezone.utc).isoformat(),
            fault=True,
            fault_reason=reason,
        )
