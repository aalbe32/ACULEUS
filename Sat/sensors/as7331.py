"""AS7331 UV (A/B/C) sensor driver.

Uses the official Adafruit library:
    pip install adafruit-circuitpython-as7331
"""
import logging

import adafruit_as7331

from sensors.base import Sensor, SensorReading

log = logging.getLogger(__name__)


class AS7331Sensor(Sensor):
    """UVA / UVB / UVC irradiance (µW/cm²) plus die temperature (°C)."""

    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None

    def initialise(self) -> bool:
        try:
            self._dev = adafruit_as7331.AS7331(
                self._i2c, address=self.config.i2c_address
            )
            # Defaults are fine for now. Tune later if channels saturate
            # in direct sunlight, e.g.:
            #   self._dev.gain = adafruit_as7331.GAIN_256X
            #   self._dev.integration_time = adafruit_as7331.TIME_64MS
            log.info(f"{self.name} initialised at 0x{self.config.i2c_address:02X}")
            return True
        except Exception as e:
            log.error(f"{self.name} failed to initialise: {e}")
            return False

    def read(self) -> SensorReading:
        if self._dev is None:
            return self.make_fault("device not initialised")

        try:
            uva, uvb, uvc = self._dev.one_shot()
            values = {
                "uva_uw_cm2": float(uva),
                "uvb_uw_cm2": float(uvb),
                "uvc_uw_cm2": float(uvc),
                "temperature_c": float(self._dev.temperature),
            }
            return self.make_reading(values)
        except Exception as e:
            return self.make_fault(f"read error: {e}")
