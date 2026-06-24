import logging
import time

from sensors.base import SensorReading, Sensor
from i2c_helpers import I2CHelpers

log = logging.getLogger(__name__)
# --- Registers ------------------------------------------------------------
REG_WHO_AM_I   = 0x4F
REG_CFG_A      = 0x60
REG_CFG_B      = 0x61
REG_CFG_C      = 0x62
REG_STATUS     = 0x67
REG_OUT_DATA   = 0x68   # 6 bytes: XL, XH, YL, YH, ZL, ZH
REG_TEMP_OUT   = 0x6E   # 2 bytes: TL, TH (little-endian, signed)
 
# --- Expected chip identity ----------------------------------------------
EXPECTED_WHO_AM_I = 0x40
 
# --- STATUS_REG bits -----------------------------------------------------
STATUS_ZYXDA = 0x08   # bit 3: new XYZ data available (cleared on data read)
 
# --- CFG_REG_A bit fields ------------------------------------------------
CFG_A_COMP_TEMP_EN = 0x80   # bit 7: temperature compensation (REQUIRED = 1 as sat will change temp in flight)
CFG_A_REBOOT       = 0x40   # bit 6: reboot memory content
CFG_A_SOFT_RST     = 0x20   # bit 5: soft reset, self-clearing
CFG_A_LP           = 0x10   # bit 4: low-power mode (0 = high-res)
 
# ODR (bits 3:2)
ODR_10HZ  = 0b00 << 2
ODR_20HZ  = 0b01 << 2
ODR_50HZ  = 0b10 << 2
ODR_100HZ = 0b11 << 2
 
# MD (bits 1:0)
MD_CONTINUOUS = 0b00
MD_SINGLE     = 0b01
MD_IDLE       = 0b11   # default at power-on


# --- CFG_REG_C bit fields ------------------------------------------------
CFG_C_BDU = 0x10   # bit 4: block data update — pairs hi/lo bytes atomically
 
# --- Scaling -------------------------------------------------------------
# Sensitivity is fixed at 1.5 mgauss/LSB. 1 gauss = 100 µT.
# So: 1 LSB = 0.0015 gauss = 0.15 µT.
SCALE_UT_PER_LSB = 0.0015 * 100.0
 
# Temperature: 8 LSB/°C, 12-bit signed, centered around 25°C.
TEMP_LSB_PER_C = 8.0
TEMP_OFFSET_C  = 25.0
 
# --- Our configuration ---------------------------------------------------
# Continuous mode at 10 Hz, high-resolution, temp comp on. At 1 Hz polling
# we'll always see fresh data on the next read.
CFG_A_VALUE = CFG_A_COMP_TEMP_EN | ODR_10HZ | MD_CONTINUOUS  # 0x80
CFG_B_VALUE = 0x00                                            # no LPF, no offset cancel
CFG_C_VALUE = CFG_C_BDU                                       # 0x10
 
RESET_SETTLE_S = 0.020   # datasheet turn-on time is 9.4 ms in HR mode; pad it

class IIS2MCD(Sensor):
    """3-axis magnetic field in microtesla, plus die temperature in °C."""

    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None

    def initialise(self) -> bool:
        try:
            self._dev = I2CHelpers(self._i2c, self.config.i2c_address)

            # 1. Chip ID check — 0x40 is distinctive (unlike QMC's 0xFF, which
            # is also what a NACK'd read returns). Catches wrong-chip-at-address
            # and bus-no-ACK in one go.
            who = self._dev.read_u8(REG_WHO_AM_I)
            if who != EXPECTED_WHO_AM_I:
                log.error(
                    f"{self.name} WHO_AM_I 0x{who:02X} "
                    f"!= expected 0x{EXPECTED_WHO_AM_I:02X}"
                )
                self._dev = None
                return False
            

            # 2. Soft reset via CFG_REG_A bit 5. Self-clearing.
            self._dev.write_u8(REG_CFG_A, CFG_A_SOFT_RST)
            time.sleep(RESET_SETTLE_S)

            # 3. Write the real configuration.
            self._dev.write_u8(REG_CFG_A, CFG_A_VALUE)
            self._dev.write_u8(REG_CFG_B, CFG_B_VALUE)
            self._dev.write_u8(REG_CFG_C, CFG_C_VALUE)


            # 4. Verify all three latched. If COMP_TEMP_EN silently dropped,
            # every reading afterwards would drift across temperature with no
            # warning — same class of failure mode as INA226 losing its
            # calibration register.
            a_rb = self._dev.read_u8(REG_CFG_A)
            b_rb = self._dev.read_u8(REG_CFG_B)
            c_rb = self._dev.read_u8(REG_CFG_C)
            if a_rb != CFG_A_VALUE or b_rb != CFG_B_VALUE or c_rb != CFG_C_VALUE:
                log.error(
                    f"{self.name} config readback mismatch: "
                    f"CFG_A=0x{a_rb:02X} (want 0x{CFG_A_VALUE:02X}) "
                    f"CFG_B=0x{b_rb:02X} (want 0x{CFG_B_VALUE:02X}) "
                    f"CFG_C=0x{c_rb:02X} (want 0x{CFG_C_VALUE:02X})"
                )
                self._dev = None
                return False

            # all passed sensor inits 
            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X} "
                f"(WHO_AM_I=0x{who:02X}, ODR=10Hz, high-res, "
                f"temp-comp on, BDU on, verified)"
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
            # Gate on DRDY. 10 Hz ODR, we should always
            # see fresh data — missing DRDY suggests the chip stopped sampling.
            status = self._dev.read_u8(REG_STATUS)
            if not (status & STATUS_ZYXDA):
                return self.make_fault("no new data (Zyxda=0)")

            # Six bytes in one transaction — atomic on the chip, so X/Y/Z
            # come from the same sample. BDU additionally guarantees that
            # the high byte matches the low byte if the chip updated mid-read.
            buf = self._dev.read_bytes(REG_OUT_DATA, 6)
            x_raw = int.from_bytes(buf[0:2], "little", signed=True)
            y_raw = int.from_bytes(buf[2:4], "little", signed=True)
            z_raw = int.from_bytes(buf[4:6], "little", signed=True)

            # Die temperature — 12-bit signed, 8 LSB/°C, centered on 25°C.
            tbuf = self._dev.read_bytes(REG_TEMP_OUT, 2)
            t_raw = int.from_bytes(tbuf, "little", signed=True)
            temp_c = TEMP_OFFSET_C + (t_raw / TEMP_LSB_PER_C)

            values = {
                "mag_x_ut":      x_raw * SCALE_UT_PER_LSB,
                "mag_y_ut":      y_raw * SCALE_UT_PER_LSB,
                "mag_z_ut":      z_raw * SCALE_UT_PER_LSB,
                "temperature_c": temp_c,
            }
            log.debug(
                f"{self.name} x={values['mag_x_ut']:.2f} "
                f"y={values['mag_y_ut']:.2f} z={values['mag_z_ut']:.2f} \u00B5T "
                f"temp={temp_c:.1f}\u00B0C"
            )
            return self.make_reading(values)

        except OSError as e:
            # Bus glitch — drop the device so next cycle re-runs initialise(),
            # which performs a soft reset. Same recovery pattern as INA226/QMC.
            self._dev = None
            log.warning(f"{self.name} read failed ({e}) — reinit next cycle")
            return self.make_fault(f"read error: {e}")
