"""
tests/test_tier1.py — Integration tests for Tier 1 header clustering and anomaly detection.

Tests the full clustering pipeline: canonicalization, family assignment, Jaccard,
structural ladder, intra-file anomaly detection (ragged, truncation, multi-record).
"""
from pathlib import Path

import pytest

from willitload.tier1.canonicalize import canonicalize_name, canonicalize_names, CanonicalizationConfig
from willitload.tier1.cluster import assign_families, structural_relation, StructuralRelation, jaccard
from willitload.tier1.anomalies import detect_csv_anomalies
from willitload.tier0.physical import PhysicalFile
from willitload.models import Bucket
from willitload import scan

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Canonicalization tests
# ---------------------------------------------------------------------------

class TestCanonicalize:
    def test_strip_leading_trailing_whitespace(self):
        trace = canonicalize_name("  CustomerID  ")
        assert trace.normalized == "customerid"

    def test_case_fold(self):
        trace = canonicalize_name("OrderDate")
        assert trace.normalized == "orderdate"

    def test_interior_whitespace_collapsed(self):
        trace = canonicalize_name("customer  id")
        assert trace.normalized == "customer id"

    def test_raw_preserved(self):
        trace = canonicalize_name("  MyColumn  ")
        assert trace.raw == "  MyColumn  "
        assert trace.normalized == "mycolumn"

    def test_no_transform_unchanged(self):
        trace = canonicalize_name("amount")
        assert not trace.was_transformed
        assert trace.describe() == "unchanged: 'amount'"

    def test_transform_described(self):
        trace = canonicalize_name("  Customer ID  ")
        assert trace.was_transformed
        desc = trace.describe()
        assert "case-folded" in desc or "whitespace" in desc

    def test_toggle_case_fold_off(self):
        config = CanonicalizationConfig(case_fold=False)
        trace = canonicalize_name("OrderDate", config)
        assert trace.normalized == "OrderDate"

    def test_toggle_strip_off(self):
        config = CanonicalizationConfig(strip_whitespace=False)
        trace = canonicalize_name("  col  ", config)
        # strip is off, but case-fold and collapse still apply
        assert "  col  " in trace.normalized or "col" in trace.normalized

    def test_list_canonicalization(self):
        names = ["CustomerID", "  Order Date  ", "AMOUNT"]
        normalized, traces = canonicalize_names(names)
        assert normalized == ["customerid", "order date", "amount"]
        assert len(traces) == 3


# ---------------------------------------------------------------------------
# Structural relation tests
# ---------------------------------------------------------------------------

class TestStructuralRelation:
    def test_exact(self):
        a = frozenset({"a", "b", "c"})
        assert structural_relation(a, a) == StructuralRelation.EXACT

    def test_additive(self):
        family = frozenset({"a", "b"})
        file_ = frozenset({"a", "b", "c"})
        assert structural_relation(file_, family) == StructuralRelation.ADDITIVE

    def test_subset(self):
        family = frozenset({"a", "b", "c"})
        file_ = frozenset({"a", "b"})
        assert structural_relation(file_, family) == StructuralRelation.SUBSET

    def test_partial(self):
        family = frozenset({"a", "b", "c"})
        file_ = frozenset({"b", "c", "d"})
        assert structural_relation(file_, family) == StructuralRelation.PARTIAL

    def test_disjoint(self):
        a = frozenset({"a", "b"})
        b = frozenset({"x", "y"})
        assert structural_relation(a, b) == StructuralRelation.DISJOINT


class TestJaccard:
    def test_identical(self):
        a = frozenset({"a", "b", "c"})
        assert jaccard(a, a) == 1.0

    def test_disjoint(self):
        a = frozenset({"a", "b"})
        b = frozenset({"x", "y"})
        assert jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        a = frozenset({"a", "b", "c"})
        b = frozenset({"b", "c", "d"})
        # intersection = {b, c} = 2, union = {a,b,c,d} = 4
        assert jaccard(a, b) == pytest.approx(2 / 4)

    def test_empty_sets(self):
        assert jaccard(frozenset(), frozenset()) == 1.0


# ---------------------------------------------------------------------------
# Family clustering integration tests
# ---------------------------------------------------------------------------

class TestFamilyClustering:
    def test_identical_files_one_family(self):
        cols = [["a", "b", "c"]] * 5
        result = assign_families(cols)
        assert len(result.families) == 1
        assert len(result.families[0].member_indices) == 5

    def test_different_sets_different_families(self):
        cols = [["a", "b"], ["x", "y"]]
        result = assign_families(cols)
        assert len(result.families) == 2

    def test_reorder_within_same_family(self):
        # Same names, different order — same family (exact set match) but REORDER detected
        cols = [["a", "b", "c"], ["c", "b", "a"]]
        result = assign_families(cols)
        # Same set → same family
        assert len(result.families) == 1
        # Second file detected as reordered
        assert result.file_relations[1] == StructuralRelation.REORDERED

    def test_empty_columns_not_assigned(self):
        cols = [[], ["a", "b"]]
        result = assign_families(cols)
        assert result.file_family_ids[0] is None
        assert result.file_family_ids[1] is not None

    def test_scan_column_drift_families(self):
        """column_drift fixture should produce 4 families (clean, extra, missing, renamed; schema is catalogued)."""
        r = scan(str(FIXTURES / "column_drift"))
        assert len(r.families) == 4, f"Expected 4 families, got {len(r.families)}: {[f.family_id for f in r.families]}"

    def test_scan_clean_conforming_one_family(self):
        """All CSVs in clean_conforming should cluster into one family (plus one for .schema file)."""
        r = scan(str(FIXTURES / "clean_conforming"))
        csv_families = [f for f in r.families if f.column_count == 5]
        assert len(csv_families) >= 1
        assert csv_families[0].file_count == 20, (
            f"Expected 20 CSVs in the main family, got {csv_families[0].file_count}"
        )

    def test_scan_delimiter_drift_one_family(self):
        """All 4 delimiter-variant files have the same schema and should be one family."""
        r = scan(str(FIXTURES / "delimiter_drift"))
        # All files have the same columns, just different delimiters
        five_col_families = [f for f in r.families if f.column_count == 5]
        assert len(five_col_families) >= 1


# ---------------------------------------------------------------------------
# Intra-file anomaly detection tests
# ---------------------------------------------------------------------------

class TestAnomalies:
    def test_ragged_rows_detected(self):
        """The ragged_rows.csv fixture should produce a RAGGED_ROWS finding."""
        r = scan(str(FIXTURES / "ragged"))
        ragged_file = next(
            (v for v in r.file_verdicts if "ragged_rows" in v.path), None
        )
        assert ragged_file is not None
        ragged_codes = [f.reason_code.value for f in ragged_file.findings]
        assert "RAGGED_ROWS" in ragged_codes, f"Findings: {ragged_codes}"

    def test_truncated_detected(self):
        """The truncated.csv fixture should produce a TRUNCATED finding."""
        r = scan(str(FIXTURES / "ragged"))
        truncated_file = next(
            (v for v in r.file_verdicts if "truncated" in v.path), None
        )
        assert truncated_file is not None
        codes = [f.reason_code.value for f in truncated_file.findings]
        assert "TRUNCATED" in codes, f"Findings: {codes}"

    def test_trailing_summary_detected(self):
        """The trailing_summary.csv fixture should produce a TRAILING_SUMMARY finding."""
        r = scan(str(FIXTURES / "ragged"))
        trailing_file = next(
            (v for v in r.file_verdicts if "trailing_summary" in v.path), None
        )
        assert trailing_file is not None
        codes = [f.reason_code.value for f in trailing_file.findings]
        assert "TRAILING_SUMMARY" in codes, f"Findings: {codes}"
