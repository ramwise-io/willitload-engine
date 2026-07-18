"""
tests/test_integrity.py — Unit and integration tests for full-file encoding validation and Gzip archive health checks.
"""
from pathlib import Path
import codecs
import gzip
import pytest

from willitload.tier0.resolver import resolve
from willitload.core import check
from willitload.baseline.flat import parse_flat_schema
from willitload.models import Bucket, ReasonCode, Verdict, AlignmentMode, ExtraColumnPolicy


def test_healthy_utf8_passes(tmp_path):
    """A healthy UTF-8 text file passes check mode."""
    file_path = tmp_path / "healthy.csv"
    file_path.write_text("customer_id,name\n123,José\n456,María\n", encoding="utf-8")

    # Define baseline
    schema_path = tmp_path / "schema.txt"
    schema_path.write_text("customer_id INT\nname TEXT\n", encoding="utf-8")
    baseline = parse_flat_schema(schema_path)

    # Run check
    res = check(str(file_path), baseline)
    assert res.has_errors is False
    assert len(res.golden) == 1
    assert len(res.broken) == 0


def test_utf8_bad_byte_later_fails(tmp_path):
    """A UTF-8 file with a corrupt byte sequence after the 64KB mark fails with DECODE_ERROR."""
    file_path = tmp_path / "corrupt_later.csv"
    
    # Write 90KB of valid CSV headers/data, followed by a corrupt byte, followed by more text
    header = b"customer_id,name\n"
    valid_row = b"123,Jose\n"
    
    with open(file_path, "wb") as f:
        f.write(header)
        for _ in range(10000):  # 10000 * 9 bytes = ~90KB (safely > 64KB)
            f.write(valid_row)
        # Write bad byte sequence (0xff is invalid in UTF-8)
        f.write(b"999,Bad\xffByte\n")
        f.write(b"1000,Jose\n")

    # Define baseline
    schema_path = tmp_path / "schema.txt"
    schema_path.write_text("customer_id INT\nname TEXT\n", encoding="utf-8")
    baseline = parse_flat_schema(schema_path)

    # Run check
    res = check(str(file_path), baseline)
    assert res.has_errors is True
    assert len(res.broken) == 1
    broken_file = res.broken[0]
    assert broken_file.bucket == Bucket.REFUSED
    
    findings = [f for f in broken_file.findings if f.reason_code == ReasonCode.DECODE_ERROR]
    assert len(findings) == 1
    assert "utf-8" in findings[0].explanation


def test_healthy_gzip_passes(tmp_path):
    """A healthy Gzipped CSV file passes check mode."""
    file_path = tmp_path / "healthy.csv.gz"
    csv_content = b"customer_id,name\n123,Jose\n456,Maria\n"
    
    with gzip.open(file_path, "wb") as gf:
        gf.write(csv_content)

    # Define baseline
    schema_path = tmp_path / "schema.txt"
    schema_path.write_text("customer_id INT\nname TEXT\n", encoding="utf-8")
    baseline = parse_flat_schema(schema_path)

    # Run check
    res = check(str(file_path), baseline)
    assert res.has_errors is False
    assert len(res.golden) == 1


def test_truncated_gzip_fails(tmp_path):
    """A truncated/corrupted Gzipped CSV file fails with CORRUPT_ARCHIVE."""
    file_path = tmp_path / "corrupt.csv.gz"
    csv_content = b"customer_id,name\n" + b"123,Jose\n" * 1000
    
    compressed = gzip.compress(csv_content)
    # Write only half of the compressed bytes (truncated)
    with open(file_path, "wb") as f:
        f.write(compressed[:len(compressed) // 2])

    schema_path = tmp_path / "schema.txt"
    schema_path.write_text("customer_id INT\nname TEXT\n", encoding="utf-8")
    baseline = parse_flat_schema(schema_path)

    # Run check
    res = check(str(file_path), baseline)
    assert res.has_errors is True
    assert len(res.broken) == 1
    broken_file = res.broken[0]
    assert broken_file.bucket == Bucket.REFUSED
    
    findings = [f for f in broken_file.findings if f.reason_code == ReasonCode.CORRUPT_ARCHIVE]
    assert len(findings) == 1


def test_gzip_bad_decode_fails(tmp_path):
    """A Gzipped file that decompresses fine but contains decode violations fails with DECODE_ERROR."""
    file_path = tmp_path / "bad_decode.csv.gz"
    
    # 90KB of valid CSV, then bad byte
    csv_parts = [b"customer_id,name\n"]
    for _ in range(10000):  # 10000 * 9 bytes = ~90KB
        csv_parts.append(b"123,Jose\n")
    csv_parts.append(b"999,Bad\xffByte\n")
    
    csv_content = b"".join(csv_parts)
    with gzip.open(file_path, "wb") as gf:
        gf.write(csv_content)

    schema_path = tmp_path / "schema.txt"
    schema_path.write_text("customer_id INT\nname TEXT\n", encoding="utf-8")
    baseline = parse_flat_schema(schema_path)

    # Run check
    res = check(str(file_path), baseline)
    assert res.has_errors is True
    assert len(res.broken) == 1
    broken_file = res.broken[0]
    assert broken_file.bucket == Bucket.REFUSED
    
    findings = [f for f in broken_file.findings if f.reason_code == ReasonCode.DECODE_ERROR]
    assert len(findings) == 1


def test_boundary_straddle_regression(tmp_path):
    """
    Regression guard: A multi-byte character straddling the 1MB chunk boundary must PASS.
    If we used simple chunk.decode(), this would throw a false positive UnicodeDecodeError.
    """
    file_path = tmp_path / "straddle.csv"
    
    # We want to put a 2-byte UTF-8 character (like 'é' which is b'\xc3\xa9')
    # such that the first byte lies at index 1,048,575 (offset 1MB - 1)
    # and the second byte lies at index 1,048,576.
    
    # Chunk size used in resolver is 1024 * 1024 = 1,048,576 bytes
    target_offset = 1048576 - 1
    
    # 1. Write headers: customer_id,name\n
    header = b"customer_id,name\n"
    
    # 2. Pad with valid ASCII text until target_offset - 1
    # We use a row format like '1,A\n' (4 bytes)
    row = b"1,A\n"
    padding_needed = target_offset - len(header)
    rows_count = padding_needed // len(row)
    
    padding_data = row * rows_count
    
    # remaining bytes to align exactly to target_offset
    rem = padding_needed - len(padding_data)
    extra_padding = b"x" * rem
    
    # At target_offset: write 'é' (b'\xc3\xa9')
    char_straddle = b"\xc3\xa9"
    
    # End row with a newline
    end_row = b"\n"
    
    content = header + padding_data + extra_padding + char_straddle + end_row
    
    # Let's verify our math:
    # index of first byte of char_straddle in content:
    assert len(header + padding_data + extra_padding) == target_offset
    
    with open(file_path, "wb") as f:
        f.write(content)

    # Define baseline
    schema_path = tmp_path / "schema.txt"
    schema_path.write_text("customer_id INT\nname TEXT\n", encoding="utf-8")
    baseline = parse_flat_schema(schema_path)

    # Run check. Since it is a valid UTF-8 file, it should PASS!
    res = check(str(file_path), baseline)
    assert res.has_errors is False
    assert len(res.golden) == 1
    assert len(res.broken) == 0
