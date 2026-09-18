# test_gps_debug.py
import serial
import time
import sys

PORT = "/dev/serial0"   # use the symlink, not ttyAMA0 directly
BAUD = 115200

def try_baud(baud):
    print(f"\n--- Trying {baud} baud ---")
    ser = serial.Serial(PORT, baud, timeout=1.0)
    ser.reset_input_buffer()
    
    start = time.monotonic()
    total = 0
    printable_dollars = 0
    
    while time.monotonic() - start < 5:
        chunk = ser.read(256)
        if chunk:
            total += len(chunk)
            printable_dollars += chunk.count(b"$")
            # Show first 100 bytes as both hex and ascii
            if total <= 100:
                print(f"  hex:   {chunk.hex(' ')}")
                print(f"  ascii: {chunk.decode('ascii', errors='replace')!r}")
    
    ser.close()
    print(f"  Total: {total} bytes, {printable_dollars} '$' chars in 5 seconds")
    return total, printable_dollars

def main():
    print("Testing all common GPS baud rates on", PORT)
    print("Expecting NMEA (lines starting with '$') at one of these rates.\n")
    
    results = {}
    for baud in [9600, 38400, 57600, 115200]:
        try:
            total, dollars = try_baud(baud)
            results[baud] = (total, dollars)
        except Exception as e:
            print(f"  ERROR at {baud}: {e}")
            results[baud] = (0, 0)
    
    print("\n=== Summary ===")
    for baud, (total, dollars) in results.items():
        note = ""
        if total == 0:
            note = "silence"
        elif dollars > 0:
            note = f"NMEA! ({dollars} '$' chars — this is the right baud)"
        elif total > 20:
            note = "bytes present but no '$' — probably wrong baud"
        print(f"  {baud:6d} baud: {total:5d} bytes {note}")

if __name__ == "__main__":
    main()