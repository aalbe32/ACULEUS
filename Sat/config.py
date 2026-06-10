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
]