# test_process_detector.py
import time
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.services.process_detector import ProcessDetector

def main():
    exec_path = "D:\\PI_CAR\\detector_cpp\\build\\Release\\detector_ncnn.exe"

    det = ProcessDetector(exec_path=exec_path, args=[], logger=None)
    print("Before start")
    det.start()
    print("After start")

    t0 = time.time()
    while time.time() - t0 < 5:
        msg = det.poll(timeout=0.5)
        if msg:
            print("GOT:", msg)
    det.stop()

if __name__ == "__main__":
    main()
