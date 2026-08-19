"""
MCP9601 thermocouple I2C amplifier driver 

The MCP9601 shares its register map with the mcp9600 so they use the same
adafruit lib - adafruit-circuitpython-mcp9600


Reports hot-junction and cold-junction temperatures and the delta betweeen them 

"""


import logging
import time

import adafruit_mcp9600

from sensors.base import Sensor, SensorReading

log = logging.getLogger(__name__)

class MCP9601(Sensor):
    "MCP9601 thermocouple I2C amplifier driver"


    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None

    def initialise(self, config, ) -> bool:
        """Creat the device object"""
        try:
            self._dev = adafruit_mcp9600.MCP9600(
                self._i2c, address=self.config.i2c_address
            )

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
            return self.make_fault("device is not initalised")

        try:
            hot_junction_c = float(self._dev.temperature)
            ambient_c = float(self._dev.ambient_temperature)
            delta_c = float(self._dev.delta_temperature)

            values = {
                "hot_junction_c": hot_junction_c,
                "ambient_c" : ambient_c,
                "delta_c" : delta_c
            }

            log.debug(
                f"{self.name} hot={hot_junction_c}C"
                f"ambient={ambient_c:.2f}C delta={delta_c:.2f}C"
            )

            return self.make_reading(values)

        except Exception as e:
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) - chip reset next cycle")
            return self.make_fault(f"read error: {e}") 
        

