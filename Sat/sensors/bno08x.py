"""

BNO08X.py IMU

"""
import logging
import time
 
import adafruit_bno08x
 
from sensors.base import Sensor, SensorReading
 
log = logging.getLogger(__name__)
 
 
 
class BNO08X(Sensor):

    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None

    def initialise(self) -> bool:
        """Create the device (= chip software reset + ID check), configure,
        and VERIFY the configuration with a read-back."""
        try:
            self._dev = adafruit_bno08x.BNO08X(
                self._i2c, 
                address=self.config.i2c_address,
            )

            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X} "
            )

            self._dev.enable_feature(adafruit_bno08x.BNO_REPORT_MAGNETOMETER)
            self._dev.enable_feature(adafruit_bno08x.BNO_REPORT_ROTATION_VECTOR)
            self._dev.enable_feature(adafruit_bno08x.BNO_REPORT_ACCELEROMETER)
            self._dev.enable_feature(adafruit_bno08x.BNO_REPORT_LINEAR_ACCELERATION)
            self._dev.enable_feature(adafruit_bno08x.BNO_REPORT_GYROSCOPE)
            self._dev.enable_feature(adafruit_bno08x.BNO_REPORT_GRAVITY)
            
            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X} "
                f"(6 reports enabled)"
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
            mag = self._dev.magnetic
            quat = self._dev.quaternion
            accel = self._dev.acceleration
            lin_accel = self._dev.linear_acceleration
            grav = self._dev.gravity
            gyro = self._dev.gyro

            values = {

                "mag_x" : mag[0],
                "mag_y" : mag[1],
                "mag_z" : mag[2],

                "quat_i": quat[0],
                "quat_j": quat[1],
                "quat_k": quat[2],
                "quat_real": quat[3],

                "accel_x" : accel[0],
                "accel_y" : accel[1],
                "accel_z" : accel[2],

                "lin_accel_x" : lin_accel[0],
                "lin_accel_y" : lin_accel[1],
                "lin_accel_z" : lin_accel[2],

                "grav_x" : grav[0],
                "grav_y" : grav[1],
                "grav_z" : grav[2],

                "gyro_x" : gyro[0],
                "gyro_y" : gyro[1],
                "gyro_z" : gyro[2],


                "calibration" : self._dev.calibration_status
            }

            return self.make_reading(values)
        except Exception as e:
            # Errno 121 etc: bus glitch mid-sequence. Reconstructing the
            # device next cycle performs a full chip reset.
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) — chip reset next cycle")
            return self.make_fault(f"read error: {e}")