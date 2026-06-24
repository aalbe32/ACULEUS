"""ACULEUS Satellite Sensor Logger — main entry point.
 
Synchronous design: one loop reads every sensor, hands each reading
to the pipeline, which verifies and writes it to the database.
"""
import logging
import signal
import sys
import time
 
import board, digitalio
 
from config import SENSORS, READ_INTERVAL_S
from database import Database
from pipeline import PipelineStats, process_reading

from sensors.as7331 import AS7331
from sensors.ina226 import INA226
from sensors.mcp9808 import MCP9808
from sensors.bno08x import BNO08X
from sensors.qmc5883l import QMC5883L
from sensors.max31855 import MAX31855
from sensors.iis2mdc import IIS2MCD


from threads.SensorThread import SensorThread
from telemetry import TelemetrySink
 
# Map config sensor names to driver classes. Add new sensors here.
SENSOR_DRIVERS = {
    "AS7331": AS7331,
    "INA226": INA226,
    "MCP9808": MCP9808,
    "BNO08X" : BNO08X,
    "QMC5883L": QMC5883L,
    "MAX31855": MAX31855,
    "IIS2MDC" : IIS2MCD,
}


# Supervisor: how often main wakes to check threads.
SUPERVISOR_TICK_S = 0.5
# How often to emit a sensors health summary.
HEALTH_LOG_INTERVAL_S = 30.0
# Grace period for a sensor thread to finish its current read on stop.
THREAD_JOIN_TIMEOUT_S = 2.0
 
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
    
    # 3. SPI bus
    log.info("Initialising SPI bus...")
    try:
        spi = board.SPI()  # uses the board's default SCL/SDA pins
    except Exception as e:
        log.critical(f"SPI init failed: {e}")
        return None
    
    # 4. Telemetry
    log.info("Initialising Telemetry sink")
    try:
        tel.open()
    except Exception as e:
        log.critical(f"Telemetry sink failed: {e}")
 
    # 5. Sensors
    sensors = []
    for sensor_config in SENSORS:
        if not sensor_config.enabled:
            log.info(f"Skipping disabled sensor: {sensor_config.name}")
            continue
 
        driver_class = SENSOR_DRIVERS.get(sensor_config.name)
        if driver_class is None:
            log.warning(f"No driver registered for {sensor_config.name} — skipping")
            continue

        # init i2c sensors
        if sensor_config.bus == "i2c":
            sensor = driver_class(sensor_config, i2c)
            log.info(f"Initialising {sensor_config.name}...")
        
        # init spi sensor at cs address
        elif sensor_config.bus == "spi":
            cs = digitalio.DigitalInOut(getattr(board, f"D{sensor_config.spi_cs_pin}"))
            sensor = driver_class(sensor_config, spi, cs)
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

def start_threads(sensors, sinks, stats):
    """Wrap each sensor in a SensorThread and start it."""
    threads = []
    for sensor in sensors:
        t = SensorThread(sensor, sinks, stats)
        t.start()
        threads.append(t)
    log.info(f"Started {len(threads)} sensor thread(s)")
    return threads


def log_health(threads, stats):
    """Single-line fleet summary + per-sensor detail for any unhealthy ones."""
    healthy = sum(1 for t in threads if t.healthy)
    total_reads = sum(t.reads for t in threads)
    total_overruns = sum(t.overruns for t in threads)
    log.info(
        f"Health: {healthy}/{len(threads)} sensors healthy | "
        f"reads={total_reads} overruns={total_overruns} | "
        f"records: processed={stats.records_processed} "
        f"dropped={stats.records_dropped} "
        f"checksum_failures={stats.checksum_failures}"
    )
    for t in threads:
        if not t.healthy:
            r = t.latest
            if r is None:
                reason = "no readings yet"
            elif r.fault:
                reason = f"last reading was fault: {r.fault_reason}"
            else:
                age = time.monotonic() - r.timestamp_monotonic
                reason = f"reading stale ({age:.1f}s old)"
            log.warning(f"  {t.name}: {reason}")


def supervise(threads, stats):
    """Main-thread supervisor loop. Wakes every SUPERVISOR_TICK_S.

    - Periodic health log.
    - Detect any sensor thread that died unexpectedly (it shouldn't —
    SensorThread.run() catches exceptions — but if the interpreter
    itself crashes a thread we want to know).
    """
    last_health_log = time.monotonic()
    while _running:
        time.sleep(SUPERVISOR_TICK_S)
 
        now = time.monotonic()
        if now - last_health_log >= HEALTH_LOG_INTERVAL_S:
            log_health(threads, stats)
            last_health_log = now
 
        for t in threads:
            if not t.is_alive():
                log.error(f"{t.name} died unexpectedly")

 
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


def stop_threads(threads):
    """Signal every thread to stop in parallel, then wait for each in turn."""
    if not threads:
        return
    log.info(f"Stopping {len(threads)} sensor thread(s)...")
    for t in threads:
        t.stop()
    for t in threads:
        t.join(timeout=THREAD_JOIN_TIMEOUT_S)
        if t.is_alive():
            log.warning(
                f"{t.name} did not stop within {THREAD_JOIN_TIMEOUT_S}s "
                f"(daemon thread will exit with process)"
            )
 
 
def shutdown(db, tel, threads, stats):
    log.info("Shutting down...")
    # ORDER MATTERS: stop sensor threads first so nothing is mid-write,
    # THEN close the sinks. Closing a sink while a worker is calling
    # write() would race against the sink's own lock and either drop
    # data or raise.
    stop_threads(threads)
    tel.shutdown()
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
    threads = []
 
    sensors = startup(db, tel)
    if sensors is None:
        log.critical("Startup failed — exiting")
        # Sinks may be partially open; close anything that opened.
        tel.shutdown()
        db.shutdown()
        return 1
 
    try:
        threads = start_threads(sensors, sinks, stats)
        supervise(threads, stats)
    finally:
        shutdown(db, tel, threads, stats)
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())