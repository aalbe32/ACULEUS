"""Per-sensor worker thread.
 
One thread per sensor. Reads at `read_rate_hz` and writes every reading
to the sinks. The latest reading is also exposed via the `latest`
attribute for downstream (e.g. telemetry batching).
"""
import logging
import threading
import time
from typing import List, Optional
 
from pipeline import PipelineStats, process_reading
from sensors.base import Sensor, SensorReading
 
log = logging.getLogger(__name__)
 
 
class SensorThread(threading.Thread):
 
    def __init__(self, sensor: Sensor, sinks: List, stats: PipelineStats):
        super().__init__(daemon=True, name=f"sensor-{sensor.name}")

        rate_hz = sensor.config.read_rate_hz
        if rate_hz is None or rate_hz <= 0:
            raise ValueError(
                f"{sensor.name}: read_rate_hz must be > 0, got {rate_hz!r}"
            )

        self.sensor = sensor
        self.sinks = sinks
        self.stats = stats
        self.interval = 1.0 / rate_hz
        self.latest: Optional[SensorReading] = None
        self.reads = 0
        self.overruns = 0
        self._stop_event = threading.Event()

    @property
    def healthy(self) -> bool:
        """True if the last reading was a non-fault and arrived recently."""
        r = self.latest  # snapshot — avoids races on individual attrs
        if r is None or r.fault:
            return False
        # Allow up to 2 intervals of slack before declaring stale.
        return (time.monotonic() - r.timestamp_monotonic) < (2 * self.interval)
 
    def run(self) -> None:
       
        log.info(
            f"{self.name} started — {1.0/self.interval:.2f} Hz "
            f"(interval={self.interval*1000:.1f} ms)"
        )
        while not self._stop_event.is_set():
            cycle_start = time.monotonic()

            try:
                reading = self.sensor.read()
                self.latest = reading   # single-reference write is atomic in CPython
                process_reading(reading, self.sinks, self.stats)
                self.reads += 1

            except Exception as e:
                # Drivers are expected to catch their own exceptions and
                # return a fault reading.
                log.exception(f"{self.name} read() raised — synthesising fault")
                reading = self.sensor.make_fault(f"unhandled read exception: {e}")
            

            elapsed = time.monotonic() - cycle_start
            remaining = self.interval - elapsed
            if remaining > 0:
                # Interruptible sleep — stop() unblocks us immediately.
                self._stop_event.wait(remaining)
            else:
                # Overran the budget. Log first occurrence and every
                # 100th after, so a persistently slow sensor doesn't
                # drown the log.
                self.overruns += 1
                if self.overruns == 1 or self.overruns % 100 == 0:
                    log.warning(
                        f"{self.name} cycle overran by "
                        f"{-remaining*1000:.1f} ms (overruns={self.overruns})"
                    )

        log.info(
            f"{self.name} stopped — reads={self.reads} overruns={self.overruns}"
        )
 
    def stop(self) -> None:
        self._stop_event.set()