"""QMC5883L 3-axis magnetometer driver.
 
driver via I2CRegisters.
 
The QMC5883L registers used:
 
    0x00-0x05  DATA           — X, Y, Z as signed int16 LITTLE-endian
    0x06       STATUS         — bit0=DRDY, bit1=OVL, bit2=DOR
    0x09       CONTROL_1      — mode, ODR, range, oversample
    0x0A       CONTROL_2      — soft reset bit
    0x0B       SET/RESET PER  — datasheet-required magic value 0x01
    0x0D       CHIP_ID        — should read 0xFF
 
Note the data registers are LITTLE-endian, unlike most other I2C sensors
(INA226, BMP, etc. are big-endian). I2CRegisters' read_u16/read_s16 helpers
default to big-endian, so we use read_bytes() + manual decode here.
 
I2C address is fixed at 0x0D (not strappable). If you see 0x1E instead,
that's an HMC5883L — a different chip with a different register map.
"""
import logging
import time
 
from config import (
    QMC5883L_RANGE_GAUSS,
    QMC5883L_OUTPUT_RATE_HZ,
    QMC5883L_OVERSAMPLE,
)
from sensors.base import Sensor, SensorReading
from i2c_helpers import I2CHelpers
 
log = logging.getLogger(__name__)
 
# --- Registers ------------------------------------------------------------
REG_DATA       = 0x00   # 6 bytes: XL, XH, YL, YH, ZL, ZH
REG_STATUS     = 0x06
REG_CONTROL_1  = 0x09
REG_CONTROL_2  = 0x0A
REG_SET_RESET  = 0x0B
REG_CHIP_ID    = 0x0D

# --- Expected chip identity ----------------------------------------------
EXPECTED_CHIP_ID = 0xFF

# --- Datasheet-required SET/RESET period --------------------------------
SET_RESET_VALUE = 0x01


# --- Status register bits ------------------------------------------------
STATUS_DRDY = 0x01   # data ready (cleared on data-register read)
STATUS_OVL  = 0x02   # at least one axis saturated
STATUS_DOR  = 0x04   # data overrun — host missed a sample


# --- CONTROL_1 bit fields ------------------------------------------------
#   bit 7:6 = OSR (oversample)
#   bit 5:4 = RNG (range)
#   bit 3:2 = ODR (output data rate)
#   bit 1:0 = MODE
MODE_STANDBY    = 0b00
MODE_CONTINUOUS = 0b01
 
ODR_10HZ  = 0b00
ODR_50HZ  = 0b01
ODR_100HZ = 0b10
ODR_200HZ = 0b11
 
RNG_2G = 0b00   # 12000 LSB/gauss → finest resolution, Earth-field range
RNG_8G = 0b01   #  3000 LSB/gauss → headroom for stronger fields
 
OSR_512 = 0b00   # cleanest
OSR_256 = 0b01
OSR_128 = 0b10
OSR_64  = 0b11

# --- CONTROL_2 bits ------------------------------------------------------
CTRL2_SOFT_RESET = 0x80

# --- Translation from config.py to bit patterns ---------
_ODR_LOOKUP = {10: ODR_10HZ, 50: ODR_50HZ, 100: ODR_100HZ, 200: ODR_200HZ}
_OSR_LOOKUP = {512: OSR_512, 256: OSR_256, 128: OSR_128, 64: OSR_64}
_RNG_LOOKUP = {2: RNG_2G, 8: RNG_8G}


# Scale factor: microtesla per raw LSB. 1 gauss = 100 µT.
# Sensitivity is 12000 LSB/gauss at ±2G, 3000 LSB/gauss at ±8G.
_SCALE_LOOKUP_UT_PER_LSB = {
    2: 100.0 / 12000.0,
    8: 100.0 /  3000.0,
}



 
class QMC5883L(Sensor):
    """3-axis magnetic field in microtesla, plus overflow/data-ready flags."""
 
    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None
 
        # Validate config choices at construction so a typo doesn't wait
        # until first read to surface.
        if QMC5883L_RANGE_GAUSS not in _RNG_LOOKUP:
            raise ValueError(
                f"QMC5883L_RANGE_GAUSS must be 2 or 8, got {QMC5883L_RANGE_GAUSS}"
            )
        if QMC5883L_OUTPUT_RATE_HZ not in _ODR_LOOKUP:
            raise ValueError(
                f"QMC5883L_OUTPUT_RATE_HZ must be 10/50/100/200, "
                f"got {QMC5883L_OUTPUT_RATE_HZ}"
            )
        if QMC5883L_OVERSAMPLE not in _OSR_LOOKUP:
            raise ValueError(
                f"QMC5883L_OVERSAMPLE must be 64/128/256/512, "
                f"got {QMC5883L_OVERSAMPLE}"
            )
 
        self._scale_ut = _SCALE_LOOKUP_UT_PER_LSB[QMC5883L_RANGE_GAUSS]
        self._control_1 = (
            (_OSR_LOOKUP[QMC5883L_OVERSAMPLE]      << 6)
          | (_RNG_LOOKUP[QMC5883L_RANGE_GAUSS]     << 4)
          | (_ODR_LOOKUP[QMC5883L_OUTPUT_RATE_HZ]  << 2)
          | (MODE_CONTINUOUS                       << 0)
        )

    def initialise(self) -> bool:
        try:
            self._dev = I2CHelpers(self._i2c, self.config.i2c_address)
 
            # Chip-ID check — 0xFF is a weak ID (it's also what a NACK'd
            # read can return), but catches "wrong chip at this address"
            # and "address didn't ACK at all".
            chip_id = self._dev.read_u8(REG_CHIP_ID)
            if chip_id != EXPECTED_CHIP_ID:
                log.error(
                    f"{self.name} chip ID 0x{chip_id:02X} "
                    f"!= expected 0x{EXPECTED_CHIP_ID:02X}"
                )
                self._dev = None
                return False
            
            
            #  soft reset - self clearing so no need to set low 
            self._dev.write_u8(REG_CONTROL_2, CTRL2_SOFT_RESET)
            time.sleep(0.01)

            # SET/RESET period
            self._dev.write_u8(REG_SET_RESET, SET_RESET_VALUE)

            # CONTROL_1 tun
            self._dev.write_u8(REG_CONTROL_1, self._control_1)
 
            # Verify CONTROL_1 stuck — same readback safety net as INA226
            cfg_rb = self._dev.read_u8(REG_CONTROL_1)
            if cfg_rb != self._control_1:
                log.error(
                    f"{self.name} CONTROL_1 readback mismatch: "
                    f"got 0x{cfg_rb:02X}, wrote 0x{self._control_1:02X}"
                )
                self._dev = None
                return False
 
            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X} "
                f"(range=\u00B1{QMC5883L_RANGE_GAUSS}G, "
                f"odr={QMC5883L_OUTPUT_RATE_HZ}Hz, "
                f"osr={QMC5883L_OVERSAMPLE}, "
                f"ctrl1=0x{self._control_1:02X} verified)"
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
            status = self._dev.read_u8(REG_STATUS)
            if not (status & STATUS_DRDY):
                # At 1Hz polling and >=10Hz ODR we should ALWAYS see fresh
                # data. Missing DRDY suggests the chip stopped sampling.
                return self.make_fault("no new data (DRDY=0)")
 
            overflow = bool(status & STATUS_OVL)
 
            # One I2C transaction for all six bytes — atomic on the chip,
            # so X/Y/Z come from the same sample.
            buf = self._dev.read_bytes(REG_DATA, 6)
            x_raw = int.from_bytes(buf[0:2], "little", signed=True)
            y_raw = int.from_bytes(buf[2:4], "little", signed=True)
            z_raw = int.from_bytes(buf[4:6], "little", signed=True)
 
            values = {
                "mag_x_ut": x_raw * self._scale_ut,
                "mag_y_ut": y_raw * self._scale_ut,
                "mag_z_ut": z_raw * self._scale_ut,
            }
            log.debug(
                f"{self.name} x={values['mag_x_ut']:.2f} "
                f"y={values['mag_y_ut']:.2f} z={values['mag_z_ut']:.2f} \u00B5T "
                f"overflow={overflow}"
            )
            return self.make_reading(values, is_anomalous=overflow)
 
        except OSError as e:
            self._dev = None   # next cycle re-runs initialise()
            log.warning(f"{self.name} read failed ({e}) — reinit next cycle")
            return self.make_fault(f"read error: {e}")