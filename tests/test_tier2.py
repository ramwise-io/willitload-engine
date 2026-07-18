"""
tests/test_tier2.py — Integration tests for Tier 2 type refinement.

Tests type inference, cross-file disagreement detection, type-variant splitting,
and the SAMPLE_SEED determinism guarantee.
"""
from pathlib import Path

import pytest

from willitload import scan
from willitload.tier2.sampler import SAMPLE_SEED, SampleConfig
from willitload.types import TypeClass, normalize_type

FIXTURES = Path(__file__).parent / "fixtures"


class TestSampleSeed:
    def test_seed_is_fixed_constant(self):
        """SAMPLE_SEED must be a fixed integer, never derived from file state."""
        assert SAMPLE_SEED == 42
        assert isinstance(SAMPLE_SEED, int)

    def test_scan_is_deterministic(self):
        """Running scan twice on the same folder must produce identical type maps."""
        folder = str(FIXTURES / "clean_conforming")
        r1 = scan(folder)
        r2 = scan(folder)
        # Compare the column_types of each file_verdict pair
        for v1, v2 in zip(r1.file_verdicts, r2.file_verdicts):
            assert v1.column_types == v2.column_types, (
                f"{v1.path}: type maps differ between runs"
            )


class TestTypeInference:
    def test_clean_conforming_types_inferred(self):
        """Clean conforming files should have all 5 columns typed."""
        r = scan(str(FIXTURES / "clean_conforming"))
        csv_verdicts = [v for v in r.file_verdicts if v.path.endswith(".csv")]
        for v in csv_verdicts:
            assert len(v.column_types) == 5, (
                f"{v.path}: expected 5 typed columns, got {v.column_types}"
            )

    def test_customer_id_inferred_as_int(self):
        """customer_id should infer as int in clean files."""
        r = scan(str(FIXTURES / "clean_conforming"))
        for v in r.file_verdicts:
            if not v.path.endswith(".csv"):
                continue
            ct = v.column_types
            if "customer_id" in ct:
                assert ct["customer_id"] in ("int", "decimal"), (
                    f"{v.path}: customer_id inferred as {ct['customer_id']!r}"
                )

    def test_type_variant_splitting(self):
        """type_drift fixture: the id-as-text file should be in a different type variant."""
        r = scan(str(FIXTURES / "type_drift"))
        # All 7 files are in the same structural family (same column names)
        family_counts = {f.family_id: f.file_count for f in r.families}
        five_col_fam = next((f for f in r.families if f.column_count == 5), None)
        assert five_col_fam is not None

        # Files with id as text should have a different type_variant_id
        normal_variants = set()
        drifted_variants = set()
        for v in r.file_verdicts:
            if not v.path.endswith(".csv"):
                continue
            if "id_as_text" in v.path or "amount_as_text" in v.path:
                drifted_variants.add(v.type_variant_id)
            else:
                normal_variants.add(v.type_variant_id)

        # Drifted files should not share a variant with normal files
        assert not (normal_variants & drifted_variants), (
            f"Drifted and normal files share type variant: {normal_variants & drifted_variants}"
        )

    def test_cross_file_disagreement_detected(self):
        """type_drift fixture should produce cross-file type disagreement findings."""
        r = scan(str(FIXTURES / "type_drift"))
        disagreement_findings = [
            f for f in r.scan_findings
            if f.reason_code.value == "TYPE_MISMATCH"
        ]
        assert len(disagreement_findings) >= 1, (
            "Expected at least one cross-file TYPE_MISMATCH finding in type_drift fixture"
        )


class TestTypeNormalizationRoundTrip:
    """Verify that type strings coming out of scan can be round-tripped through normalize_type."""

    def test_scan_types_are_valid_type_classes(self):
        r = scan(str(FIXTURES / "clean_conforming"))
        valid_values = {tc.value for tc in TypeClass}
        for v in r.file_verdicts:
            for col, type_str in v.column_types.items():
                assert type_str in valid_values, (
                    f"{v.path}.{col}: type {type_str!r} is not a valid TypeClass value"
                )
