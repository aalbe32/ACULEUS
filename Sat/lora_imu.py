import json
import logging
import math
import threading
import time
 
from config import (
    LORA_SCK_PIN, LORA_MOSI_PIN, LORA_MISO_PIN,
    LORA_CS_PIN, LORA_RESET_PIN, LORA_FREQ_MHZ, LORA_TX_POWER,
)
from pipeline import PipelineRecord
 
log = logging.getLogger(__name__)
 
IMU_NAME = "BNO08X"
MAX_PAYLOAD = 240   # RFM9x limit is 252
 
# packet key -> BNO08X value keys, in order
FIELDS = {
    "m": ("mag_x", "mag_y", "mag_z"),
    "q": ("quat_i", "quat_j", "quat_k", "quat_real"),
    "a": ("accel_x", "accel_y", "accel_z"),
    "l": ("lin_accel_x", "lin_accel_y", "lin_accel_z"),
    "g": ("grav_x", "grav_y", "grav_z"),
    "r": ("gyro_x", "gyro_y", "gyro_z"),
}
 
 
def _num(v):
    """Round to 3 dp; NaN/inf become null so the JSON stays valid."""
    if v is None or not math.isfinite(v):
        return None
    return round(v, 3)
 
 
# ---------------------- RADIO ---------------------------------------
def make_radio():
    """Build the RFM9x on a bit-banged SPI bus using GPIO pins from config.
 
    Software SPI is slow (tens of kHz in Python), but a ~220-byte packet
    every 2 s is still only a few ms of bus time. It also keeps the radio
    off the hardware SPI bus, so it never contends with the MAX31855.
    """
    import board
    import digitalio
    import adafruit_bitbangio as bitbangio
    import adafruit_rfm9x
 
    pin = lambda n: getattr(board, f"D{n}")
    spi = bitbangio.SPI(pin(LORA_SCK_PIN), MOSI=pin(LORA_MOSI_PIN), MISO=pin(LORA_MISO_PIN))
    cs = digitalio.DigitalInOut(pin(LORA_CS_PIN))
    reset = digitalio.DigitalInOut(pin(LORA_RESET_PIN))
 
    rfm = adafruit_rfm9x.RFM9x(spi, cs, reset, LORA_FREQ_MHZ, baudrate=1_000_000)
    rfm.tx_power = LORA_TX_POWER
    log.info(
        f"RFM9x on bit-banged SPI (SCK=D{LORA_SCK_PIN} MOSI=D{LORA_MOSI_PIN} "
        f"MISO=D{LORA_MISO_PIN} CS=D{LORA_CS_PIN} RST=D{LORA_RESET_PIN}) "
        f"at {LORA_FREQ_MHZ} MHz, {LORA_TX_POWER} dBm"
    )
    return rfm
 
 
# ---------------------- SINK ---------------------------------------
class LoRaSink:
 
    def __init__(self, radio, tx_interval_s: float = 2.0):
        if radio is None:
            raise ValueError("LoRaSink needs a radio")
        self._radio = radio
        self._tx_interval = tx_interval_s
        self._latest = None                 # latest BNO08X record
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._seq = 0
 
    def open(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lora", daemon=True)
        self._thread.start()
        log.info("LoRa thread started")
 
    def write(self, record: PipelineRecord) -> None:
        if record.sensor_name != IMU_NAME:
            return
        with self._lock:
            self._latest = record
 
    def shutdown(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        log.info("LoRa sink closed")
 
    # ---------------------- THREAD ---------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._transmit()
            except Exception as e:
                log.error(f"LoRa error: {e}")
            # Sleep out the rest of the interval. wait() returns early on
            # shutdown, so stopping never takes longer than one transmit.
            self._stop.wait(max(0.0, self._tx_interval - (time.monotonic() - started)))
 
    def _transmit(self) -> None:
        with self._lock:
            r = self._latest
        if r is None:
            return                          # no IMU reading yet
 
        self._seq = (self._seq + 1) & 0xFFFF
        if r.fault:
            body = {"s": self._seq, "f": r.fault_reason[:64]}
        else:
            body = {"s": self._seq}
            for short, keys in FIELDS.items():
                body[short] = [_num(r.values.get(k)) for k in keys]
            body["c"] = r.values.get("calibration")
 
        payload = json.dumps(body, separators=(",", ":")).encode()
        if len(payload) > MAX_PAYLOAD:
            log.warning(f"LoRa packet too big ({len(payload)} bytes) — not sent")
            return
        self._radio.send(payload)

