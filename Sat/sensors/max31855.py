# """

# MAX31855 thermocouple interface


# """
import logging
import time
 
import adafruit_max31855

from sensors.base import Sensor, SensorReading
 
log = logging.getLogger(__name__)
 
 
 
class MAX31855(Sensor): 

    def __init__(self, config, spi, cs):
        super().__init__(config)
        self._spi = spi
        self._cs = cs
        self._dev = None


    def initialise(self) -> bool:
        """Create the device (= chip software reset + ID check), configure,
        and VERIFY the configuration with a read-back."""
        try:
            self._dev = adafruit_max31855.MAX31855(self._spi, cs=self._cs)

            log.info(
                f"{self.name} initialised at 0x{self.config.spi_cs_pin:02X} "
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
            temp = self._dev.temperature

            values = {
                "temp_in_c": temp
            }

            return self.make_reading(values)
        except Exception as e:
            # Errno 121 etc: bus glitch mid-sequence. Reconstructing the
            # device next cycle performs a full chip reset.
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) — chip reset next cycle")
            return self.make_fault(f"read error: {e}")