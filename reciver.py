# receiver.py
import json, socket
from  Sat.telemetry import crc16_ccitt

srv = socket.socket(); srv.bind(("0.0.0.0", 5005)); srv.listen(1)
conn, addr = srv.accept(); print(f"connected from {addr}")
buf = b""
while True:
    chunk = conn.recv(4096)
    if not chunk: break
    buf += chunk
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        obj = json.loads(line)
        sent_crc = obj.pop("crc16")
        recomputed = crc16_ccitt(json.dumps(obj, separators=(",",":")).encode())
        ok = "✓" if sent_crc == recomputed else "✗ CRC MISMATCH"
        print(ok, obj["sensor"], obj["values"])