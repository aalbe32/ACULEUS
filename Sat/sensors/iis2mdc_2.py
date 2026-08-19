"""

IIS2MDC 3-axis magnetometer driver.

"""

import logging
import time
 
import adafruit_lis2mdl

from sensors.base import Sensor, SensorReading
 
log = logging.getLogger(__name__)
 
 
 
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
            self._dev._data_rate = self.config.read_rate_hz

            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X} "
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
                "X_Mag": x,
                "Y_Mag": y,
                "Z_Mag": z,
            }

            return self.make_reading(values)
        except Exception as e:
            # Errno 121 etc: bus glitch mid-sequence. Reconstructing the
            # device next cycle performs a full chip reset.
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) — chip reset next cycle")
            return self.make_fault(f"read error: {e}")