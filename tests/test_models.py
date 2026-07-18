"""
Tests for willitload.models — typed result objects and severity projection.
"""

import pytest
from willitload.models import (
    ReasonCode,
    Severity,
    AlignmentMode,
    project_severity,
    Finding,
    FileVerdict,
    Bucket,
    Verdict,
    Accounting,
    ScanResult,
)


class TestSeverityProjection:
    def test_name_mismatch_name_mode_is_error(self):
        sev = project_severity(ReasonCode.COLUMN_NAME_MISMATCH, AlignmentMode.NAME)
        assert sev == Severity.ERROR

    def test_name_mismatch_position_mode_is_warn(self):
        sev = project_severity(ReasonCode.COLUMN_NAME_MISMATCH, AlignmentMode.POSITION)
        assert sev == Severity.WARN

    def test_missing_column_both_modes_error(self):
        for mode in AlignmentMode:
            assert project_severity(ReasonCode.MISSING_COLUMN, mode) == Severity.ERROR

    def test_ragged_rows_mode_independent(self):
        sev_name = project_severity(ReasonCode.RAGGED_ROWS, AlignmentMode.NAME)
        sev_pos = project_severity(ReasonCode.RAGGED_ROWS, AlignmentMode.POSITION)
        sev_none = project_severity(ReasonCode.RAGGED_ROWS, None)
        assert sev_name == sev_pos == sev_none == Severity.ERROR

    def test_encoding_fallback_is_warn(self):
        assert project_severity(ReasonCode.ENCODING_FALLBACK, None) == Severity.WARN

    def test_headerless_name_mode_is_error(self):
        assert project_severity(ReasonCode.HEADERLESS_NAME_MODE, None) == Severity.ERROR


class TestFinding:
    def test_to_dict_has_all_fields(self):
        f = Finding(
            reason_code=ReasonCode.MISSING_COLUMN,
            severity=Severity.ERROR,
            locus="column 'id'",
            expected="id",
            found=None,
            explanation="Column 'id' is missing.",
        )
        d = f.to_dict()
        assert d["reason_code"] == "MISSING_COLUMN"
        assert d["severity"] == "ERROR"
        assert d["locus"] == "column 'id'"
        assert d["expected"] == "id"
        assert d["found"] is None
        assert "explanation" in d
        assert d["confidence"] == 1


class TestAccounting:
    def test_reconciles_correctly(self):
        acc = Accounting(files_seen=10, profiled=7, degraded=1, catalogued=1, refused=1)
        assert acc.reconciles()

    def test_detects_non_reconciliation(self):
        acc = Accounting(files_seen=10, profiled=5, degraded=1, catalogued=1, refused=1)
        assert not acc.reconciles()


class TestScanResultJSON:
    def test_round_trip_json(self):
        import json
        acc = Accounting(files_seen=2, profiled=2, degraded=0, catalogued=0, refused=0)
        result = ScanResult(
            path_expression="./test/",
            elapsed_ms=42.5,
            accounting=acc,
            file_verdicts=[],
        )
        j = result.to_json()
        parsed = json.loads(j)
        # files_seen lives under the accounting sub-object (it's the Accounting struct's field)
        assert parsed["accounting"]["files_seen"] == 2
        assert parsed["path_expression"] == "./test/"
        assert "file_verdicts" in parsed
        assert parsed["accounting"]["reconciles"] is True
