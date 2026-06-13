"""ACULEUS Satellite Sensor Logger — main entry point.
 
Synchronous design: one loop reads every sensor, hands each reading
to the pipeline, which verifies and writes it to the database.
"""
import logging
import signal
import sys
import time
 
import board
 
from config import SENSORS, READ_INTERVAL_S
from database import Database
from pipeline import PipelineStats, process_reading

from sensors.as7331 import AS7331
from sensors.ina226 import INA226
from sensors.mcp9808 import MCP9808
from sensors.bno08x import BNO08X
from sensors.qmc5883l import QMC5883L

from telemetry import TelemetrySink
 
# Map config sensor names to driver classes. Add new sensors here.
SENSOR_DRIVERS = {
    "AS7331": AS7331,
    "INA226": INA226,
    "MCP9808": MCP9808,
    "BNO08X" : BNO08X,
    "QMC5883L": QMC5883L,
}
 
log = logging.getLogger("main")
 
_running = True  # cleared by the signal handler to end the main loop
 
 
def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
 
 
def handle_signal(signum, frame) -> None:
    global _running
    log.info(f"Received signal {signum} — stopping after current cycle")
    _running = False
 
 
def startup(db: Database, tel: TelemetrySink):
    """
    Initialise database and sensors in order.
    Returns the list of started sensors, or None if startup failed.
    """
    log.info("=" * 60)
    log.info("ACULEUS Satellite Sensor Logger starting up")
    log.info("=" * 60)
 
    # 1. Database
    log.info("Initialising database...")
    try:
        db.initialize()
    except Exception as e:
        log.critical(f"Database init failed: {e}")
        return None
 
    # 2. I2C bus
    log.info("Initialising I2C bus...")
    try:
        i2c = board.I2C()  # uses the board's default SCL/SDA pins
    except Exception as e:
        log.critical(f"I2C init failed: {e}")
        return None
    
    # 3. Telemetry
    log.info("Initialising Telemetry sink")
    try:
        tel.open()
    except Exception as e:
        log.critical(f"Telemetry sink failed: {e}")
 
    # 4. Sensors
    sensors = []
    for sensor_config in SENSORS:
        if not sensor_config.enabled:
            log.info(f"Skipping disabled sensor: {sensor_config.name}")
            continue
 
        driver_class = SENSOR_DRIVERS.get(sensor_config.name)
        if driver_class is None:
            log.warning(f"No driver registered for {sensor_config.name} — skipping")
            continue
 
        sensor = driver_class(sensor_config, i2c)
        log.info(f"Initialising {sensor_config.name}...")
 
        if not sensor.initialise():
            if sensor_config.critical:
                log.critical(
                    f"Critical sensor {sensor_config.name} failed to "
                    f"initialise — aborting startup"
                )
                return None
            log.warning(
                f"Non-critical sensor {sensor_config.name} failed to "
                f"initialise — continuing without it"
            )
            continue
 
        sensors.append(sensor)
        log.info(f"{sensor_config.name} ready")
 
    if not sensors:
        log.critical("No sensors started — aborting")
        return None
 
    log.info("=" * 60)
    log.info(f"Startup complete — {len(sensors)} sensor(s) running")
    log.info("=" * 60)
    return sensors
 
 
def run_loop(sensors, sinks: list, stats: PipelineStats) -> None:
    """Read every sensor each cycle until a stop signal arrives."""
    while _running:
        cycle_start = time.monotonic()
 
        for sensor in sensors:
            reading = sensor.read()
            process_reading(reading, sinks, stats)
 
        # Sleep out the remainder of the interval
        elapsed = time.monotonic() - cycle_start
        remaining = READ_INTERVAL_S - elapsed
        if remaining > 0:
            time.sleep(remaining)
 
 
def shutdown(db: Database, stats: PipelineStats) -> None:
    log.info("Shutting down...")
    db.shutdown()
    log.info(
        f"Final stats — processed={stats.records_processed} "
        f"dropped={stats.records_dropped} "
        f"checksum_failures={stats.checksum_failures}"
    )
    log.info("Shutdown complete")
 
 
def main() -> int:
    setup_logging()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
 
    db = Database()
    tel = TelemetrySink("192.168.4.2", 5005)
    stats = PipelineStats()

    sinks = [db, tel]
 
    sensors = startup(db, tel)
    if sensors is None:
        log.critical("Startup failed — exiting")
        db.shutdown()
        return 1
 
    try:
        run_loop(sensors, sinks, stats)
    finally:
        shutdown(db, stats)
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())