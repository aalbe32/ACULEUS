"""MCP9808 Temperature Sensor (.c)

"""
import logging
import time
 
import adafruit_mcp9808
 
from sensors.base import Sensor, SensorReading
 
log = logging.getLogger(__name__)
 
 
 
class MCP9808(Sensor):
    """Temperatur sensor"""
 
    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None
 
    def initialise(self) -> bool:
        """Create the device (= chip software reset + ID check), configure,
        and VERIFY the configuration with a read-back."""
        try:
            self._dev = adafruit_mcp9808.MCP9808(self._i2c, address=self.config.i2c_address)

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
            # make a reading 
            temp_c = float(self._dev.temperature)

            log.debug(f"{self.name} temp={temp_c:.2f}°C")
            return self.make_reading({"temperature_c": temp_c})
 
        except Exception as e:
            # Errno 121 etc: bus glitch mid-sequence. Reconstructing the
            # device next cycle performs a full chip reset.
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) — chip reset next cycle")
            return self.make_fault(f"read error: {e}")