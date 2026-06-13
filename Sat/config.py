"""ACULEUS mission configuration.
 
All project-wide constants and the sensor list live here.
"""
from dataclasses import dataclass
 
 
# Mission identity
SATELLITE_ID = "ACULEUS-1"
MISSION_NAME = "ACULEUS"
 
# Data
DB_PATH = "data.db"
SCHEMA_VERSION = 1
 
# Main loop
READ_INTERVAL_S = 1.0       # seconds between sensor read cycles
 
# MCP9808 Precision temp sensor
MCP9808_I2C_ADDRESS = 0x18

# AS7331 UV sensor
AS7331_I2C_ADDRESS = 0x74

# INA226 voltage sensor
INA226_SHUNT_OHMS = 0.2
INA226_CURRENT_LSB_A = 2
INA226_I2C_ADDRESS = 0x40

# BNO08X i2c adresses 
BNO08X_I2C_ADDRESS_1 =  0x4A
BNO08X_I2C_ADDRESS_2 =  0x4B

# QMC5883L magnetometer 
# Range: 2 G for fine resolution (Earth-field measurement), 8 G for headroom
#   near magnetic disturbances.
# Output rate: 10/50/100/200 Hz.
# Oversample: 64/128/256/512 — 512 is cleanest, costs no CPU (chip does it) but takes more power.
QMC5883L_RANGE_GAUSS    = 2
QMC5883L_OUTPUT_RATE_HZ = 10
QMC5883L_OVERSAMPLE     = 512
QMC5883L_I2C_ADDRESS = 0x0D


@dataclass(frozen=True)
class SensorConfig:
    """Static configuration for one sensor."""
    name: str
    i2c_address: int
    enabled: bool = True
    critical: bool = False   # if True, startup aborts when this sensor fails
 
 
# All sensors on the bus. Drivers are registered in ACULEUS.py.
SENSORS = [
    SensorConfig(
        name="AS7331",
        i2c_address=AS7331_I2C_ADDRESS,
        enabled=True,
        critical=True,
    ),

    SensorConfig(
        name="INA226",
        i2c_address=INA226_I2C_ADDRESS,
        enabled=True,
        critical=True
    ),

    SensorConfig(
        name="MCP9808", 
        i2c_address=MCP9808_I2C_ADDRESS,
        enabled= True,
        critical= True
    ),

    SensorConfig(
        name="BNO08X",
        i2c_address= BNO08X_I2C_ADDRESS_1,
        enabled=True,
        critical=True
    ),

    SensorConfig(
        name="QMC5883L",
        i2c_address= QMC5883L_I2C_ADDRESS,
        enabled=True,
        critical=True
    ),

]