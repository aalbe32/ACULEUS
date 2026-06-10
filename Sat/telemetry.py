"""TCP telemetry sink.

Serializes each PipelineRecord as a JSON line with a CRC-16/CCITT,
ships it over TCP, and auto-reconnects on failure.
"""
import json
import logging
import socket
from typing import Optional

from pipeline import PipelineRecord

log = logging.getLogger(__name__)

TELEMETRY_VERSION = 1
SOCKET_TIMEOUT_S = 0.2   # short — never block the read loop


def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE — matches the C util/crc16."""
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class TelemetrySink:
    """Sends each record as a JSON+CRC line over TCP."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._sent = 0
        self._failed = 0

    def open(self) -> None:
        """Best-effort connect at startup. Failure is fine — write() will retry."""
        self._connect()

    def _connect(self) -> bool:
        try:
            s = socket.create_connection((self._host, self._port), timeout=SOCKET_TIMEOUT_S)
            s.settimeout(SOCKET_TIMEOUT_S)
            self._sock = s
            log.info(f"Telemetry connected to {self._host}:{self._port}")
            return True
        except OSError as e:
            log.warning(f"Telemetry connect failed: {e}")
            self._sock = None
            return False

    def _drop(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def _build_packet(self, record: PipelineRecord) -> bytes:
        body = {
            "version": TELEMETRY_VERSION,
            "sat": record.satellite_id,
            "mission": record.mission_name,
            "sensor": record.sensor_name,
            "ts_rtc": record.timestamp_rtc,
            "ts_mono": record.timestamp_monotonic,
            "valid": not record.fault,
            "fault_reason": record.fault_reason,
            "values": record.values,
        }
        # CRC over the JSON bytes (without the crc field itself)
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        body["crc16"] = crc16_ccitt(raw)
        return (json.dumps(body, separators=(",", ":")) + "\n").encode("utf-8")

    def write(self, record: PipelineRecord) -> None:
        """Send one record. Reconnects once on failure, then gives up for this packet."""
        packet = self._build_packet(record)

        if self._sock is None and not self._connect():
            self._failed += 1
            return

        try:
            self._sock.sendall(packet)
            self._sent += 1
        except OSError as e:
            log.warning(f"Telemetry send failed ({e}) — dropping socket, will retry next packet")
            self._drop()
            self._failed += 1

    def shutdown(self) -> None:
        self._drop()
        log.info(f"Telemetry closed — sent={self._sent} failed={self._failed}")