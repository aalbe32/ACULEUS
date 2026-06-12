"""
Reusable I2C register I/O helpers.
"""
import logging
from typing import Optional

from adafruit_bus_device.i2c_device import I2CDevice

log = logging.getLogger(__name__)


class I2CHelpers:
    """Per-device register accessor. One instance per sensor.

    Usage in a driver:
        self._dev = I2CRegisters(i2c, address=0x40)
        manufacturer = self._dev.read_u16(0xFE)
        self._dev.write_u16(0x05, calibration_value)
        current = self._dev.read_s16(0x04)
    """

    def __init__(self, i2c, address: int):
        self._address = address
        self._dev = I2CDevice(i2c, address)

    @property
    def address(self) -> int:
        return self._address

    # -- 8-bit -----------------------------------------------------------

    def read_u8(self, reg: int) -> int:
        """Read one unsigned byte from `reg`."""
        buf = bytearray(1)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([reg]), buf)
        return buf[0]

    def write_u8(self, reg: int, value: int) -> None:
        """Write one byte to `reg`."""
        with self._dev as i2c:
            i2c.write(bytes([reg, value & 0xFF]))

    # -- 16-bit big-endian (most common pattern) -------------------------

    def read_u16(self, reg: int) -> int:
        """Read a 16-bit big-endian register as unsigned int."""
        return self.read_int(reg, width=2, signed=False)

    def read_s16(self, reg: int) -> int:
        """Read a 16-bit big-endian register as signed int (two's complement)."""
        return self.read_int(reg, width=2, signed=True)

    def write_u16(self, reg: int, value: int) -> None:
        """Write a 16-bit big-endian register."""
        self.write_int(reg, value, width=2)

    # -- Generic width (for 24-bit / 32-bit registers on other chips) ----

    def read_int(self, reg: int, width: int = 2, signed: bool = False) -> int:
        """Read a `width`-byte big-endian register."""
        buf = bytearray(width)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([reg]), buf)
        return int.from_bytes(buf, "big", signed=signed)

    def write_int(self, reg: int, value: int, width: int = 2) -> None:
        """Write a `width`-byte big-endian register."""
        payload = value.to_bytes(width, "big", signed=value < 0)
        with self._dev as i2c:
            i2c.write(bytes([reg]) + payload)

    # -- Raw bulk access (for sensors that stream multi-byte results) ----

    def read_bytes(self, reg: int, n: int) -> bytes:
        """Read `n` bytes starting at `reg`, no decoding."""
        buf = bytearray(n)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([reg]), buf)
        return bytes(buf)

    def write_bytes(self, reg: int, payload: bytes) -> None:
        """Write raw bytes to `reg`."""
        with self._dev as i2c:
            i2c.write(bytes([reg]) + payload)