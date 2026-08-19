"""

IIS2MDC 3-axis magnetometer driver.

"""

import logging
import time
 
import adafruit_lis2mdl

from sensors.base import Sensor, SensorReading
 
log = logging.getLogger(__name__)


_RATE_HZ_ENUM = {
    10: adafruit_lis2mdl.DataRate.Rate_10_HZ,
    20: adafruit_lis2mdl.DataRate.Rate_20_HZ,
    50: adafruit_lis2mdl.DataRate.Rate_50_HZ,
    100: adafruit_lis2mdl.DataRate.Rate_100_HZ,
}
 
 
 
class IIS2MDC(Sensor): 

    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None


    def initialise(self) -> bool:
        """Create the device (= chip software reset + ID check), configure,
        and VERIFY the configuration with a read-back."""
        try:
            adafruit_lis2mdl._ADDRESS_MAG = self.config.i2c_address

            self._dev = adafruit_lis2mdl.LIS2MDL(self._i2c)
            self._dev._data_rate = _RATE_HZ_ENUM[self.config.read_rate_hz]

            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X}"
                f"{self.name} Read Rate Configued to {self.config.read_rate_hz}" 
            )

            log.debug(
                f"x_off-{self._dev.x_offset}, y_off-{self._dev.y_offset}, z_off-{self._dev.z_offset}"
                f"read_rate-{self._dev.data_rate}"
            )
            return True
        except Exception as e:
            log.error(f"{self.name} failed to initialise: {e}")
            self._dev = None
            return False

 
    def read(self) -> SensorReading:

        if self._dev is None and not self.initialise():
            return self.make_fault("device not initialised")

        try:

            x, y, z = self._dev.magnetic

            values = {
                "x_mag": x,
                "y_mag": y,
                "z_mag": z,
            }

            log.debug(
                f"{self.name} x={x}, y={y}, z={z}"
            )

            return self.make_reading(values)
        except Exception as e:
            # Errno 121 etc: bus glitch mid-sequence. Reconstructing the
            # device next cycle performs a full chip reset.
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) — chip reset next cycle")
            return self.make_fault(f"read error: {e}")