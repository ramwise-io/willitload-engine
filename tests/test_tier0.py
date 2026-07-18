"""
tests/test_tier0.py — Integration tests for Tier 0 acquisition and physical resolution.

Tests cover the full resolver pipeline: format detection, encoding detection,
delimiter inference, archive handling, file accounting, and scale limits.
Each test targets a specific fixture folder or synthetic file.
"""
import os
import json
import zipfile
from pathlib import Path

import pytest

from willitload.tier0.encoding import detect_encoding
from willitload.tier0.format_detect import detect_format
from willitload.tier0.resolver import resolve, ResolverConfig
from willitload.models import Bucket

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Encoding detection tests
# ---------------------------------------------------------------------------

class TestEncodingDetection:
    def test_utf8_file(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_bytes(b"col1,col2\nfoo,bar\n")
        enc, is_fallback = detect_encoding(str(f))
        assert enc == "utf-8"
        assert is_fallback is False

    def test_utf8_bom_file(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_bytes(b"\xef\xbb\xbfcol1,col2\nfoo,bar\n")
        enc, is_fallback = detect_encoding(str(f))
        assert enc == "utf-8-sig"
        assert is_fallback is False

    def test_utf16le_bom_file(self, tmp_path):
        f = tmp_path / "test.csv"
        content = "col1,col2\nfoo,bar\n".encode("utf-16-le")
        f.write_bytes(b"\xff\xfe" + content)
        enc, is_fallback = detect_encoding(str(f))
        assert enc == "utf-16-le"
        assert is_fallback is False

    def test_latin1_fallback(self, tmp_path):
        f = tmp_path / "test.csv"
        # Write bytes that are invalid UTF-8 (e.g. 0x80-0xff range)
        f.write_bytes(b"col1,col2\nnote_\x96_value,bar\n")
        enc, is_fallback = detect_encoding(str(f))
        assert enc == "latin-1"
        assert is_fallback is True

    def test_encoding_zoo_files(self):
        """Verify the fixture corpus encoding files are detected correctly."""
        zoo = FIXTURES / "encoding_zoo"
        assert (zoo / "utf8.csv").exists()
        assert (zoo / "utf8_bom.csv").exists()
        assert (zoo / "utf16le.csv").exists()
        assert (zoo / "latin1.csv").exists()

        enc_utf8, fb = detect_encoding(str(zoo / "utf8.csv"))
        assert enc_utf8 == "utf-8" and not fb

        enc_bom, fb = detect_encoding(str(zoo / "utf8_bom.csv"))
        assert enc_bom == "utf-8-sig" and not fb

        enc_utf16, fb = detect_encoding(str(zoo / "utf16le.csv"))
        assert enc_utf16 == "utf-16-le" and not fb

        enc_latin, fb = detect_encoding(str(zoo / "latin1.csv"))
        assert enc_latin == "latin-1" and fb is True


# ---------------------------------------------------------------------------
# Format detection tests
# ---------------------------------------------------------------------------

class TestFormatDetection:
    def test_csv_detected(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("col1,col2\nfoo,bar\nbaz,qux\n")
        fmt, conf = detect_format(f)
        assert fmt == "csv"
        assert conf >= 1

    def test_json_detected(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('[{"a": 1}, {"a": 2}]')
        fmt, conf = detect_format(f)
        assert fmt == "json"

    def test_jsonl_detected(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n')
        fmt, conf = detect_format(f)
        assert fmt == "jsonl"

    def test_zip_detected(self, tmp_path):
        f = tmp_path / "archive.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("test.csv", "col1\nval\n")
        fmt, conf = detect_format(f)
        assert fmt == "zip"
        assert conf == 2

    def test_sqlite_detected(self, tmp_path):
        import sqlite3
        f = tmp_path / "db.sqlite"
        conn = sqlite3.connect(f)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.close()
        fmt, conf = detect_format(f)
        assert fmt == "sqlite"
        assert conf == 2

    def test_extension_lying_json_named_csv(self):
        """A JSON file named .csv must be detected as JSON, not CSV."""
        lying_file = FIXTURES / "extension_lying" / "looks_like_csv.csv"
        assert lying_file.exists()
        fmt, conf = detect_format(lying_file)
        assert fmt in ("json", "jsonl"), f"Expected json/jsonl, got {fmt!r}"


# ---------------------------------------------------------------------------
# Resolver integration tests
# ---------------------------------------------------------------------------

class TestResolver:
    def test_zero_match_is_a_finding(self, tmp_path):
        r = resolve(str(tmp_path / "nonexistent_glob_*.csv"))
        assert r.files_seen == 0
        assert len(r.set_findings) >= 1
        assert any("no files" in f.explanation.lower() or "matched no files" in f.explanation.lower()
                   for f in r.set_findings)

    def test_accounting_reconciles(self):
        """files_seen == profiled + degraded + catalogued + refused for every fixture."""
        for folder in sorted(FIXTURES.iterdir()):
            if not folder.is_dir():
                continue
            r = resolve(str(folder))
            a = r.accounting
            total = a["profiled"] + a["degraded"] + a["catalogued"] + a["refused"]
            assert total == r.files_seen, (
                f"{folder.name}: {total} != {r.files_seen} "
                f"(profiled={a['profiled']}, degraded={a['degraded']}, "
                f"catalogued={a['catalogued']}, refused={a['refused']})"
            )

    def test_clean_conforming_all_profiled(self):
        """All CSV files in clean_conforming should be profiled."""
        r = resolve(str(FIXTURES / "clean_conforming"))
        csv_files = [pf for pf in r.physical_files if pf.path.suffix == ".csv"]
        profiled_csvs = [pf for pf in csv_files if pf.bucket == Bucket.PROFILED]
        assert len(profiled_csvs) == len(csv_files), (
            f"Expected all {len(csv_files)} CSVs profiled, got {len(profiled_csvs)}"
        )

    def test_clean_conforming_columns_detected(self):
        """All profiled CSVs should have the expected 5 column names."""
        r = resolve(str(FIXTURES / "clean_conforming"))
        expected_cols = {"customer_id", "order_date", "amount", "status", "notes"}
        for pf in r.physical_files:
            if pf.path.suffix == ".csv" and pf.bucket == Bucket.PROFILED:
                assert set(pf.raw_column_names) == expected_cols, (
                    f"{pf.path.name}: got {pf.raw_column_names}"
                )

    def test_delimiter_drift_all_profiled(self):
        """All 4 delimiter-variant files should be profiled."""
        r = resolve(str(FIXTURES / "delimiter_drift"))
        assert r.accounting["profiled"] == 4, (
            f"Expected 4 profiled, got {r.accounting['profiled']}"
        )

    def test_delimiter_detected_correctly(self):
        """Tab and pipe files should have the correct delimiter detected."""
        r = resolve(str(FIXTURES / "delimiter_drift"))
        delimiters = {pf.path.stem: pf.delimiter for pf in r.physical_files if pf.bucket == Bucket.PROFILED}
        assert delimiters.get("tab") == "\t", f"Expected tab delimiter, got {delimiters.get('tab')!r}"
        assert delimiters.get("pipe") == "|", f"Expected pipe delimiter, got {delimiters.get('pipe')!r}"

    def test_latin1_file_gets_encoding_fallback_bucket(self):
        """A Latin-1 file should still be in PROFILED or DEGRADED, with encoding_is_fallback=True."""
        r = resolve(str(FIXTURES / "encoding_zoo"))
        latin_files = [pf for pf in r.physical_files if pf.path.stem == "latin1"]
        assert latin_files, "latin1.csv not found in resolver results"
        latin_pf = latin_files[0]
        assert latin_pf.encoding_is_fallback is True

    def test_archive_catalogued(self):
        """ZIP archives should be in CATALOGUED bucket (v1: not recursively profiled)."""
        r = resolve(str(FIXTURES / "archive_set"))
        zip_files = [pf for pf in r.physical_files if pf.path.suffix == ".zip"]
        for zf in zip_files:
            assert zf.bucket == Bucket.CATALOGUED, (
                f"{zf.path.name}: expected CATALOGUED, got {zf.bucket}"
            )

    def test_file_count_ceiling_enforced(self, tmp_path):
        """Resolver emits FILE_COUNT_CEILING finding when ceiling is exceeded."""
        # Create 5 files, set ceiling to 3
        for i in range(5):
            (tmp_path / f"file_{i}.csv").write_text("col1\nval\n")
        config = ResolverConfig(file_count_ceiling=3)
        r = resolve(str(tmp_path), config=config)
        assert r.files_seen == 3
        ceiling_findings = [f for f in r.set_findings
                            if f.reason_code.value == "FILE_COUNT_CEILING"]
        assert len(ceiling_findings) >= 1

    def test_permission_denied_buckets_as_refused(self, tmp_path):
        """Permission-denied files should be in REFUSED bucket, not cause a crash."""
        import sys
        if sys.platform == "win32":
            pytest.skip("Windows does not support POSIX permission modes via chmod")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("Running as root; chmod 0o000 will be bypassed by the OS")

        f = tmp_path / "denied.csv"
        f.write_text("col1\nval\n")
        try:
            os.chmod(f, 0o000)
            r = resolve(str(tmp_path))
            refused = [pf for pf in r.physical_files if pf.bucket == Bucket.REFUSED]
            assert len(refused) >= 1
        finally:
            os.chmod(f, 0o644)  # restore for cleanup
