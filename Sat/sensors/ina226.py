"""INA226 voltage sensor driver.

    The INA226 measures current by reading the differential voltage across an
    external shunt resistor on the high-side of the load. The shunt resistance
    must be configured here and in hardware (same value).
 
"""
import logging

from adafruit_bus_device.i2c_device import I2CDevice

from sensors.base import Sensor, SensorReading
from config import INA226_CURRENT_LSB_A, INA226_SHUNT_OHMS
import i2c_helpers

log = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# I2C Address
INA226_ADDR_DEFAULT = 0x40   # A0=GND, A1=GND

# ---------------------------------------------------------------------------
# Chip numbers
MANUFACTURER_ID = 0x5449   # "TI" in ASCII
DIE_ID          = 0x2260


# ---------------------------------------------------------------------------
# Register Addresses
REG_CONFIG      = 0x00   # Configuration register
REG_SHUNT_V     = 0x01   # Shunt voltage (signed 16-bit, 2.5 µV/LSB)
REG_BUS_V       = 0x02   # Bus voltage (unsigned 16-bit, 1.25 mV/LSB)
REG_POWER       = 0x03   # Power (unsigned 16-bit, calibration-dependent)
REG_CURRENT     = 0x04   # Current (signed 16-bit, calibration-dependent)
REG_CALIBRATION = 0x05   # Calibration register
REG_MANUF_ID    = 0xFE   # Manufacturer ID — always 0x5449 (TI)
REG_DIE_ID      = 0xFF   # Die ID — always 0x2260


# ---------------------------------------------------------------------------
# Register Values

CONFIG_RESET = 0x8000

CALIBRATION_CONST = 0.00512

BUS_LSB_V   = 0.00125     # 1.25 mV per LSB
SHUNT_LSB_V = 0.0000025   # 2.5  µV per LSB // constant from datasheet pg 3

# ---------------------------------------------
# Averaging mode (bits 11:9) 
AVG_1    = 0b000
AVG_4    = 0b001
AVG_16   = 0b010
AVG_64   = 0b011
AVG_128  = 0b100
AVG_256  = 0b101
AVG_512  = 0b110
AVG_1024 = 0b111

# ---------------------------------------------
# Conversion time (bits 8:6 = bus, bits 5:3 = shunt) 
CT_140US  = 0b000   # noisiest, fastest
CT_204US  = 0b001
CT_332US  = 0b010
CT_588US  = 0b011
CT_1100US = 0b100   # chip default
CT_2116US = 0b101
CT_4156US = 0b110
CT_8244US = 0b111   # cleanest per sample, slowest

# ---------------------------------------------
# Operating mode (bits 2:0) 
MODE_POWER_DOWN  = 0b000
MODE_SHUNT_TRIG  = 0b001
MODE_BUS_TRIG    = 0b010
MODE_BOTH_TRIG   = 0b011
MODE_SHUNT_CONT  = 0b101
MODE_BUS_CONT    = 0b110
MODE_BOTH_CONT   = 0b111 # shunt and bus cont


AVG_MODE        = AVG_16
BUS_CONV_TIME   = CT_1100US
SHUNT_CONV_TIME = CT_1100US
OPER_MODE       = MODE_BOTH_CONT

CONFIG_VALUE = (
    (AVG_MODE        << 9)
  | (BUS_CONV_TIME   << 6)
  | (SHUNT_CONV_TIME << 3)
  | (OPER_MODE       << 0)
)


class INA226(Sensor):
    """
    Driver for the INA226 high-side power monitor.

    Measures bus voltage, shunt voltage, and uses the INA226's internal
    calibration to compute current and power directly from the hardware.

    Configured for:
      - Continuous shunt + bus voltage measurement
      - 64-sample averaging (noise rejection for satellite environment)
      - 1.1 ms conversion time per sample <- Default
      - Total cycle time ≈ 141 ms (well within 2 Hz poll interval)
      - CURRENT_LSB = 1 mA (calibration register = 512)
    """

    def __init__(self, config, i2c):
        super().__init__(config)
        self._i2c = i2c
        self._dev = None
        self._shunt_ohms = INA226_SHUNT_OHMS
        self._current_lsb = INA226_CURRENT_LSB_A
        self._power = 25.0 * INA226_CURRENT_LSB_A

    def initialise(self) -> bool:
        try: 
            self._dev = i2c_helpers.I2CHelpers(self._i2c, self.config.i2c_address)

            # write reset bit to config register to restore defaults
            self._dev.write_u16(REG_CONFIG, CONFIG_RESET)
            
            # ensure we are reading ina226 
            mfg = self._dev.read_u16(REG_MANUF_ID)
            die = self._dev.read_u16(REG_DIE_ID)
            if mfg != MANUFACTURER_ID or die != DIE_ID:
                log.error(
                    f"{self.name} ID mismatch: "
                    f"mfg=0x{mfg:04X} (want 0x{MANUFACTURER_ID:04X}) "
                    f"die=0x{die:04X} (want 0x{DIE_ID:04X})"
                )
                self._dev = None
                return False

            """
            the calibration register will be used to convert the value from the current LSB and the
            shunt resistance to compute the current in amps

            equation (1) page 14 in datasheet 

            cal = 0.00512 / current_LSB * shunt_resistance
            """ 
            #write to calibration using equation (1)
            cal = CALIBRATION_CONST / (self._current_lsb * self._shunt_ohms)
            if not 0 < cal < 0x1000:
                log.error(
                    f"{self.name} calibration value {cal} out of range"
                    f"- check shunt_ohms/current_lsb in config"
                ) 
                self._dev = None
                return False
            
            self._dev.write_u16(REG_CALIBRATION, cal)
            self._dev.write_u16(REG_CONFIG, CONFIG_VALUE)


            # read both registers and ensure correct state
            cal_reg = self._dev.read_u16(REG_CALIBRATION)
            cfg_reg = self._dev.read_u16(REG_CONFIG)
            if cal_reg != cal | cfg_reg != CONFIG_VALUE:
                log.error(
                    f"{self.name} config readback mismatch: "
                    f"cal=0x{cal_reg:04X} (want 0x{cal:04X}) "
                    f"cfg=0x{cfg_reg:04X} (want 0x{CONFIG_VALUE:04X})"
                )
                self._regs = None
                return False
            
            # successful init 
            log.info(
                f"{self.name} initialised at 0x{self.config.i2c_address:02X} "
                f"(shunt={self._shunt_ohms}\u03A9, "
                f"current_lsb={self._current_lsb * 1000}mA, "
                f"cal=0x{cal:04X} verified)"
            )
            return True
        except Exception as e:
            log.error(f"{self.name} failed to initialise: {e}")
            self._regs = None
            return False
        

    def read(self) -> SensorReading:
        if self._dev is None and not self.initialise():
            return self.make_fault("device not initialised")
 
        try:
            bus_raw     = self._dev.read_u16(REG_BUS_V)
            shunt_raw   = self._dev.read_s16(REG_SHUNT_V)
            current_raw = self._dev.read_s16(REG_CURRENT)
            power_raw   = self._dev.read_u16(REG_POWER)
 
            values = {
                "bus_voltage_v":   bus_raw * BUS_LSB_V,
                "shunt_voltage_v": shunt_raw * SHUNT_LSB_V,
                "current_a":       current_raw * self._current_lsb,
                "power_w":         power_raw * self._power_lsb,
            }
            log.debug(
                f"{self.name} bus={values['bus_voltage_v']:.3f}V "
                f"current={values['current_a']*1000:.1f}mA "
                f"power={values['power_w']*1000:.1f}mW"
            )
            return self.make_reading(values)
 
        except OSError as e:
            self._dev = None  # next cycle re-runs initialise()
            log.warning(f"{self.name} read failed ({e}) — reinit next cycle")
            return self.make_fault(f"read error: {e}")