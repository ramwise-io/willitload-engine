"""
tests/benchmark_perf.py — Pre-flight performance benchmark.

Generates 1,000 synthetic CSV files, scans them with willitload,
measures the elapsed time, and prints a formatted performance report.
Asserts that 1,000 files scan in < 5 seconds.
"""
import shutil
import tempfile
import time
from pathlib import Path

from willitload.core import scan


def generate_benchmark_files(directory: Path, count: int = 1000):
    """Write synthetic CSV files quickly."""
    header = "customer_id,order_date,amount,status,notes\n"
    row = "7311,2024-07-02,2596.32,DELIVERED,note_value\n"
    content = header + (row * 5)
    
    for i in range(count):
        filename = directory / f"orders_{i:04d}.csv"
        filename.write_text(content, encoding="utf-8")


def run_benchmark():
    print("------------------------------------------------------------")
    print("willitload Engine Performance Benchmark")
    print("------------------------------------------------------------")
    
    # 1. Setup temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        print("Generating 1,000 synthetic CSV files...")
        t0 = time.monotonic()
        generate_benchmark_files(temp_path, 1000)
        gen_time = time.monotonic() - t0
        print(f"Generated 1,000 files in {gen_time:.2f}s.")
        
        # 2. Run scan
        print("\nScanning 1,000 files via willitload...")
        t1 = time.monotonic()
        result = scan(str(temp_path))
        scan_time = time.monotonic() - t1
        
        acc = result.accounting
        
        print("------------------------------------------------------------")
        print("Performance Results:")
        print(f"  Files seen:          {acc.files_seen}")
        print(f"  Files profiled:      {acc.profiled}")
        print(f"  Total Scan Time:     {scan_time:.2f} seconds")
        print(f"  Core reported time:  {result.elapsed_ms / 1000:.2f} seconds")
        print(f"  Avg time per file:   {(scan_time / 1000) * 1000:.2f} ms")
        print("------------------------------------------------------------")
        
        # 3. Assertion
        limit = 5.0
        if scan_time < limit:
            print(f"SUCCESS: Scan completed in {scan_time:.2f}s (under {limit}s ceiling).")
            return True
        else:
            print(f"FAILURE: Scan took {scan_time:.2f}s (exceeded {limit}s ceiling).")
            return False


if __name__ == "__main__":
    run_benchmark()
