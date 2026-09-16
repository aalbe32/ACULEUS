from adafruit_extended_bus import ExtendedI2C as I2C
from adafruit_bno08x.i2c import BNO08X_I2C
from adafruit_bno08x import BNO_REPORT_ACCELEROMETER

i2c = I2C(3)
bno = BNO08X_I2C(i2c, address=0x4A)
bno.enable_feature(BNO_REPORT_ACCELEROMETER)
print(bno.acceleration)