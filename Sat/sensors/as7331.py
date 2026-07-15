"""AS7331 UV (A/B/C) sensor driver.
 
Uses the official Adafruit library (verified against v1.0.2 source):
    pip install adafruit-circuitpython-as7331
 
Notes from the library source:
- Constructing AS7331() performs a chip software reset (OSR=0x0A), full
  reconfiguration, and a chip-ID check. So re-creating the object IS a
  hard reset — that's our recovery mechanism.
- The library's one_shot() polls the NOTREADY bit, not the NDATA flag.
  If a start trigger is lost (e.g. after a bus glitch), it returns stale
  result-register contents with no error. We detect that here via the
  stuck-repeat / stuck-zero checks.
- one_shot() never checks the chip's overflow flags; we do, and mark
  the reading anomalous if any channel overflowed.
"""
import logging
import time
 
import adafruit_as7331
 
from sensors.base import Sensor, SensorReading
 
log = logging.getLogger(__name__)
 
# Gain/integration. Library defaults are GAIN_4X / TIME_64MS; we set them
# explicitly so the driver doesn't depend on library defaults, and verify
# the writes with a read-back. Full scale at these settings is ~87,000
# uW/cm2 on UVA — plenty of headroom. Raise gain later if counts are low.
GAIN = adafruit_as7331.GAIN_4X
INTEGRATION_TIME = adafruit_as7331.TIME_64MS
 
STUCK_ZERO_THRESHOLD = 3    # consecutive all-zero reads -> reinit
STUCK_REPEAT_THRESHOLD = 4  # consecutive bit-identical reads -> reinit
 
 
class AS7331(Sensor):
    """UVA / UVB / UVC irradiance (µW/cm²) plus die temperature (°C)."""
 
    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None
        self._zero_streak = 0
        self._repeat_streak = 0
        self._last_triple = None
 
    def initialise(self) -> bool:
        """Create the device (= chip software reset + ID check), configure,
        and VERIFY the configuration with a read-back."""
        try:
            self._dev = adafruit_as7331.AS7331(
                self._i2c, address=self.config.i2c_address
            )
            self._dev.gain = GAIN
            self._dev.integration_time = INTEGRATION_TIME
 
            # Read back and verify — a write that silently failed would
            # corrupt every irradiance conversion afterwards.
            g, t = self._dev.gain, self._dev.integration_time
            if g != GAIN or t != INTEGRATION_TIME:
                log.error(
                    f"{self.name} config readback mismatch: "
                    f"gain={g} (wanted {GAIN}) time={t} (wanted {INTEGRATION_TIME})"
                )
                self._dev = None
                return False
 
            time.sleep(0.05)  # let the chip settle after reset+config
            self._zero_streak = 0
            self._repeat_streak = 0
            self._last_triple = None
            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X} "
                f"(gain={g} integration_time={t} verified)"
            )
            return True
        except Exception as e:
            log.error(f"{self.name} failed to initialise: {e}")
            self._dev = None
            return False
 
    def _force_reinit(self, reason: str) -> SensorReading:
        """Drop the device so next read reconstructs it (= chip reset)."""
        log.warning(f"{self.name}: {reason} — forcing chip reset on next read")
        self._dev = None
        self._zero_streak = 0
        self._repeat_streak = 0
        self._last_triple = None
        return self.make_fault(reason)
 
    def read(self) -> SensorReading:
        if self._dev is None and not self.initialise():
            return self.make_fault("device not initialised")

        if not self._dev.data_ready:
            return self.make_fault("device is not ready with data")
    
        try:
            uva, uvb, uvc = self._dev.one_shot()
            overflow = bool(self._dev.overflow)
            log.debug(
                f"{self.name} raw: uva={uva} uvb={uvb} uvc={uvc} overflow={overflow}"
            )
            triple = (uva, uvb, uvc)
 
            # Stale-data detection: the library can return old register
            # contents without error if the measurement never triggered.
            if triple == (0.0, 0.0, 0.0):
                self._zero_streak += 1
                self._repeat_streak = 0
                if self._zero_streak >= STUCK_ZERO_THRESHOLD:
                    return self._force_reinit("stuck at zero")
            elif triple == self._last_triple:
                self._repeat_streak += 1
                self._zero_streak = 0
                if self._repeat_streak >= STUCK_REPEAT_THRESHOLD:
                    return self._force_reinit(f"stuck at {triple}")
            else:
                self._zero_streak = 0
                self._repeat_streak = 0
 
            self._last_triple = triple
 
            values = {
                "uva_uw_cm2": float(uva),
                "uvb_uw_cm2": float(uvb),
                "uvc_uw_cm2": float(uvc),
                "temperature_c": float(self._dev.temperature),
            }
            # Overflowed channels are garbage — keep the row, flag it.
            return self.make_reading(values, is_anomalous=overflow)
 
        except Exception as e:
            # Errno 121 etc: bus glitch mid-sequence. Reconstructing the
            # device next cycle performs a full chip reset.
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) — chip reset next cycle")
            return self.make_fault(f"read error: {e}")