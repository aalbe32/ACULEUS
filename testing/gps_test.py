# test_gps_01_raw_bytes.py
"""Print whatever bytes come out of the GPS. No parsing, no assumptions.

Success: you see printable ASCII starting with '$' — that's NMEA.
Failure modes:
  - Nothing at all -> wrong port, wrong wiring, module not powered
  - Garbage bytes -> wrong baud rate (try 9600, 38400, 115200)
  - '$' lines but they end in weird chars -> baud is close but not right
"""
import serial
import sys

PORT = "/dev/ttyAMA0"   # Raspberry Pi hardware UART; use /dev/ttyUSB0 for a USB-serial adapter
BAUD = 115200

def main():
    print(f"Opening {PORT} at {BAUD}...")
    ser = serial.Serial(PORT, BAUD, timeout=1.0)
    print("Reading for 10 seconds. Ctrl-C to stop early.\n")
    
    import time
    end = time.monotonic() + 10
    total_bytes = 0
    
    try:
        while time.monotonic() < end:
            chunk = ser.read(256)
            if chunk:
                total_bytes += len(chunk)
                sys.stdout.write(chunk.decode("ascii", errors="replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    
    print(f"\n\n--- Read {total_bytes} bytes total ---")
    ser.close()

if __name__ == "__main__":
    main()