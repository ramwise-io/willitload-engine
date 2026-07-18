"""
tests/test_check.py — Integration tests for check mode and the structural diff engine.

Tests name vs position alignment, extra column policy (strict vs open), rename
candidate detection, impossible-quadrant guard, type widening/breaking check.
"""
from pathlib import Path
import pytest

from willitload import check, scan
from willitload.baseline.flat import parse_flat_schema
from willitload.models import AlignmentMode, ExtraColumnPolicy, Verdict, Severity

FIXTURES = Path(__file__).parent / "fixtures"


class TestCheckMode:
    @pytest.fixture
    def baseline(self):
        return parse_flat_schema(FIXTURES / "column_drift" / "baseline.schema")

    def test_clean_conforming_passes_perfectly(self, baseline):
        """All files in clean_conforming conform to baseline under name and position mode."""
        # Clean CSVs have exact matches
        res_name = check(
            str(FIXTURES / "clean_conforming" / "orders_*.csv"),
            baseline,
            AlignmentMode.NAME,
            ExtraColumnPolicy.STRICT
        )
        assert len(res_name.broken) == 0
        assert len(res_name.warned) == 0
        assert len(res_name.golden) == 20
        assert res_name.has_errors is False

        res_pos = check(
            str(FIXTURES / "clean_conforming" / "orders_*.csv"),
            baseline,
            AlignmentMode.POSITION,
            ExtraColumnPolicy.STRICT
        )
        assert len(res_pos.broken) == 0
        assert len(res_pos.warned) == 0
        assert len(res_pos.golden) == 20

    def test_extra_column_strict_vs_open(self, baseline):
        """--extra strict fails with extra columns, --extra open passes with warnings."""
        # orders_extra.csv has one extra column "region"
        # STRICT mode -> BROKEN
        res_strict = check(
            str(FIXTURES / "column_drift" / "orders_extra.csv"),
            baseline,
            AlignmentMode.NAME,
            ExtraColumnPolicy.STRICT
        )
        assert len(res_strict.broken) == 1
        assert len(res_strict.golden) == 0
        assert any(f.reason_code.value == "EXTRA_COLUMN" and f.severity == Severity.ERROR
                   for f in res_strict.broken[0].findings)

        # OPEN mode -> WARNED (Conforms with warnings)
        res_open = check(
            str(FIXTURES / "column_drift" / "orders_extra.csv"),
            baseline,
            AlignmentMode.NAME,
            ExtraColumnPolicy.OPEN
        )
        assert len(res_open.broken) == 0
        assert len(res_open.warned) == 1
        assert len(res_open.golden) == 0
        assert any(f.reason_code.value == "EXTRA_COLUMN" and f.severity == Severity.INFO
                   for f in res_open.warned[0].findings)

    def test_missing_column_fails_always(self, baseline):
        """Missing columns must fail the check under any mode/policy."""
        # orders_missing.csv is missing "notes"
        res = check(
            str(FIXTURES / "column_drift" / "orders_missing.csv"),
            baseline,
            AlignmentMode.NAME,
            ExtraColumnPolicy.OPEN
        )
        assert len(res.broken) == 1
        assert any(f.reason_code.value == "MISSING_COLUMN" and f.severity == Severity.ERROR
                   for f in res.broken[0].findings)

    def test_impossible_quadrant_guard(self, baseline):
        """Headerless files in name-mode must fail with HEADERLESS_NAME_MODE."""
        # no_header.csv in header_chaos has no header row
        res = check(
            str(FIXTURES / "header_chaos" / "no_header.csv"),
            baseline,
            AlignmentMode.NAME,
            ExtraColumnPolicy.STRICT
        )
        assert len(res.broken) == 1
        assert any(f.reason_code.value == "HEADERLESS_NAME_MODE" and f.severity == Severity.ERROR
                   for f in res.broken[0].findings)

    def test_rename_evidence_extracted(self, baseline):
        """Rename candidates (same position, different name, similar/compatible type) surfaced as WARN."""
        # orders_renamed.csv renamed amount -> total_amount
        res = check(
            str(FIXTURES / "column_drift" / "orders_renamed.csv"),
            baseline,
            AlignmentMode.NAME,
            ExtraColumnPolicy.STRICT
        )
        # It's broken overall because "amount" is missing and "total_amount" is extra (under strict policy)
        assert len(res.broken) == 1
        # It must extract rename evidence
        findings = res.broken[0].findings
        rename_findings = [f for f in findings if f.reason_code.value == "COLUMN_NAME_MISMATCH"]
        assert len(rename_findings) >= 1
        assert rename_findings[0].severity == Severity.WARN
        assert rename_findings[0].expected == "amount"
        assert rename_findings[0].found == "total_amount"

    def test_position_alignment_detects_count_mismatch(self, baseline):
        """In position mode, column count change is flagged."""
        res = check(
            str(FIXTURES / "column_drift" / "orders_missing.csv"),
            baseline,
            AlignmentMode.POSITION,
            ExtraColumnPolicy.STRICT
        )
        assert len(res.broken) == 1
        findings = res.broken[0].findings
        assert any(f.reason_code.value == "COUNT_CHANGED" and f.severity == Severity.ERROR
                   for f in findings)
