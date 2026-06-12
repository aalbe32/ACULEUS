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
 
# INA226 voltage sensor
INA226_SHUNT_OHMS = 0.2
INA226_CURRENT_LSB_A = 2

#BNO08X i2c adresses 
BNO08X_I2C_ADRESS_1 =  0x4A
BNO08X_I2C_ADRESS_2 =  0x4B



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
        i2c_address=0x74,
        enabled=True,
        critical=True,
    ),

    SensorConfig(
        name="INA226",
        i2c_address=0x40,
        enabled=True,
        critical=True
    ),

    SensorConfig(
        name="MCP9808", 
        i2c_address=0x18,
        enabled= True,
        critical= True
    ),

    SensorConfig(
        name="BNO08X",
        i2c_address= BNO08X_I2C_ADRESS_1,
        enabled=True,
        critical=True
    ),
]