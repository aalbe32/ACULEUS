


import logging
import time

import serial
import pynmea2
from sensors.base import Sensor, SensorReading

log = logging.getLogger(__name__)


class PX1122C(Sensor):


    def __int__(self, config): 
        super().__init__(config)
        self._ser = None

    def initialise(self) -> bool:

        try:
            self._ser = serial.Serial(
                self.config.uart_port,
                self.config.uart_baud,
                timeout=1.0
            )
            self._ser.reset_input_buffer()
            log.info(
                f"{self.name} initalised on {self.config.uart_port}"
                f"@ {self.config.uart_port} buad"
            ) 

            return True
        
        except Exception as e:
            log.error(f"{self.name} failed to initalise: {e}")
            self._ser = None
            return False

    def read(self) -> SensorReading:
        if self._ser is None and not self.initialise():
            return self.make_fault("device not initialised")

        try:
            # Read whatever's in the buffer right now. NMEA at 1Hz means
            # roughly 200-400 bytes are usually waiting.
            chunk = self._ser.read(self._ser.in_waiting or 1)

            # Split into lines, find the last GGA
            for line in reversed(chunk.decode("ascii", errors="ignore").splitlines()):
                if line.startswith("$") and "GGA" in line[:7]:
                    try:
                        msg = pynmea2.parse(line)
                    except pynmea2.ParseError:
                        continue

                    if not msg.gps_qual or int(msg.gps_qual) == 0:
                        return self.make_fault("no fix")

                    return self.make_reading({
                        "lat_deg": float(msg.latitude),
                        "lon_deg": float(msg.longitude),
                        "alt_m": float(msg.altitude or 0.0),
                        "fix_quality": int(msg.gps_qual),
                        "sats_used": int(msg.num_sats or 0),
                        "hdop": float(msg.horizontal_dil or 99.9),
                    }, is_anomalous=(int(msg.gps_qual) < 2))

            return self.make_fault("no GGA in buffer")

        except Exception as e:
            self._ser = None
            return self.make_fault(f"read error: {e}")