import logging
import queue
import threading
import time
import json

from sensors.base import SensorReading


log = logging.getLogger(__name__)

class LoRaSink:

    def __init__(self, radio=None, tx_interval_s: float = 2.0):
        self._radio = radio
        self._tx_interval = tx_interval_s

        self._snapshot = {} # sensor_name {lastest record}

        self._snapshot_lock = threading.Lock()
        self.command_queue = queue.Queue(maxsize=32)

        self._stop = threading.Event()
        self._thread = None
        self._seq = 0

    # ---------------------- INTERFACE ---------------------------------------
    @property
    def radio(self):
        return self._radio
    
    def open(self) -> None:
        "clear stop and start thread"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lora", daemon=True)
        self._thread.start()
        log.info("LoRa thread started...")

    def write(self, record: SensorReading) -> None:
        "called from sensor thread returns a dict with the name of the sensor as a key and assosiated values"
        with self._snapshot_lock:
            self._snapshot = [record.sensor_name] = record


    def shutdown(self) -> None:
        "gracfull close, join threads"
        with self._snapshot_lock is None:
            return
        self._stop.set()
        self._thread.join(timeout=2.0)
        log.info("LoRa sink closed")

    # ---------------------- THREAD ---------------------------------------
    def _loop(self) -> None:
        last_tx = 0.0
        while not self._stop.is_set():
            if time.monotonic() - last_tx >= self._tx_interval:
                self._transmit()
                last_tx = time.monotonic()
            self._receive(timeout_s=0.2)

    def _transmit(self) -> None:
        with self._snapshot_lock:
            snap = dict(self._snapshot)

        self._seq = (self._seq + 1) & 0xFFFF
        body = {
            "seq": self._seq,
            "sensors": {
                name: ({"fault": r.fault_reason} if r.fault else r.values)
                for name, r in snap.items()
            },
        }
        self._radio.send(json.dumps(body).encode())

    def _receive(self, timeout_s: float) -> None:
        packet = self._radio.receive(timeout_s)
        if packet is None:
            return
        try:
            cmd = json.loads(packet)
        except Exception as e:
            log.warning(f"Bad command packet: {e}")
            return
        try:
            self.command_queue.put_nowait(cmd)
            log.info(f"Command queued: {cmd}")
        except queue.Full:
            log.warning("Command queue full — dropping")