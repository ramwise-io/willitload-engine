"""
tests/test_baseline.py — Tests for all three baseline front-doors.

Verifies that flat schema files, prior-scan JSON, and golden sample files
all normalize to identical BaselineFingerprint shapes, and that the
fingerprint carries no behavior (mode/policy are always CLI flags).
"""
import json
from pathlib import Path

import pytest

from willitload.baseline.flat import parse_flat_schema
from willitload.baseline.from_json import parse_from_scan_json
from willitload.baseline.golden import parse_golden_file
from willitload.baseline.fingerprint import BaselineFingerprint
from willitload.types import TypeClass
from willitload import scan

FIXTURES = Path(__file__).parent / "fixtures"


class TestFlatSchemaParser:
    def test_basic_parse(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("customer_id,int\norder_date,date\namount,decimal\n")
        fp = parse_flat_schema(schema)
        assert fp.column_count == 3
        assert fp.ordered_names == ["customer_id", "order_date", "amount"]
        assert fp.ordered_types == [TypeClass.INT, TypeClass.DATE, TypeClass.DECIMAL]

    def test_alias_normalization(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("id,bigint\nname,varchar\nactive,boolean\n")
        fp = parse_flat_schema(schema)
        assert fp.ordered_types == [TypeClass.INT, TypeClass.TEXT, TypeClass.BOOL]

    def test_tab_separator(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("col_a\tint\ncol_b\ttext\n")
        fp = parse_flat_schema(schema)
        assert fp.column_count == 2
        assert fp.ordered_types[0] == TypeClass.INT

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("# header comment\ncol_a,int\n\n# another comment\ncol_b,text\n")
        fp = parse_flat_schema(schema)
        assert fp.column_count == 2

    def test_name_only_lines_default_to_any(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("col_a\ncol_b\n")
        fp = parse_flat_schema(schema)
        assert all(t == TypeClass.ANY for t in fp.ordered_types)

    def test_column_order_preserved(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("z_col,int\na_col,text\nm_col,date\n")
        fp = parse_flat_schema(schema)
        assert fp.ordered_names == ["z_col", "a_col", "m_col"]

    def test_names_are_canonicalized(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("CustomerID,int\n  Order Date  ,date\n")
        fp = parse_flat_schema(schema)
        assert fp.ordered_names == ["customerid", "order date"]

    def test_column_drift_fixture_baseline(self):
        """Baseline from column_drift fixture is parseable."""
        fp = parse_flat_schema(FIXTURES / "column_drift" / "baseline.schema")
        assert fp.column_count == 5
        assert "customer_id" in fp.name_set

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Cannot read"):
            parse_flat_schema(tmp_path / "nonexistent.schema")

    def test_empty_file_raises(self, tmp_path):
        schema = tmp_path / "empty.schema"
        schema.write_text("")
        with pytest.raises(ValueError, match="no data lines"):
            parse_flat_schema(schema)


class TestFromJsonParser:
    def test_round_trip_from_scan_result(self, tmp_path):
        """Scan a folder, save JSON, reload as baseline — should reproduce the schema."""
        r = scan(str(FIXTURES / "clean_conforming"))
        json_path = tmp_path / "scan_result.json"
        json_path.write_text(r.to_json())

        fp = parse_from_scan_json(json_path)
        assert fp.column_count == 5
        assert "customer_id" in fp.name_set

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Cannot read"):
            parse_from_scan_json(tmp_path / "nonexistent.json")

    def test_invalid_json_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json {{{{")
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_from_scan_json(bad)

    def test_wrong_shape_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"hello": "world"}))
        with pytest.raises(ValueError, match="does not look like"):
            parse_from_scan_json(bad)


class TestGoldenFileParser:
    def test_golden_csv_file(self):
        """A clean CSV can be used as a golden baseline."""
        golden = FIXTURES / "clean_conforming" / "orders_001.csv"
        fp = parse_golden_file(golden)
        assert fp.column_count == 5
        assert "customer_id" in fp.name_set

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            parse_golden_file(tmp_path / "nonexistent.csv")

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_text("")
        with pytest.raises(ValueError, match="could not be profiled"):
            parse_golden_file(f)


class TestBaselineBehaviorFree:
    """Baseline carries no behavior — same baseline, different modes via CLI flags."""

    def test_fingerprint_has_no_alignment_mode(self):
        fp = parse_flat_schema(FIXTURES / "column_drift" / "baseline.schema")
        assert not hasattr(fp, "alignment_mode"), "BaselineFingerprint must not carry alignment mode"
        assert not hasattr(fp, "extra_column_policy"), "BaselineFingerprint must not carry extra-column policy"

    def test_source_description_populated(self, tmp_path):
        schema = tmp_path / "test.schema"
        schema.write_text("col_a,int\n")
        fp = parse_flat_schema(schema)
        assert "flat schema file" in fp.source_description
