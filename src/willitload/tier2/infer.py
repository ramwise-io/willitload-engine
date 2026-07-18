"""
willitload.tier2.infer — Type inference refinement and cross-file disagreement detection.

Takes Tier-1 families and runs type sampling within each family (never across families).
Produces:
  - Type-variant splitting: "same header, same types" vs "same header, column X differs"
  - Cross-file type-inference disagreement: "id: int in 40 files, text in 3 files"
  - Per-family type profile

Scope guard (§10):
  Type inference is ONLY used to sharpen grouping and verdicts.
  No distribution analysis, no value-range validation, no content sketches.
  Nullability is observed and reported as an observation — NOT enforced.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import duckdb

from willitload.models import Finding, ReasonCode, Severity, project_severity, AlignmentMode
from willitload.tier0.physical import PhysicalFile
from willitload.tier1.cluster import FamilyCandidate
from willitload.tier2.sampler import (
    SampleConfig,
    FileSample,
    sample_csv_types,
    sample_parquet_types,
)
from willitload.types import TypeClass, check_compatibility, Compatibility


@dataclass
class TypeVariant:
    """A distinct type profile within a family."""
    variant_id: str
    family_id: str
    column_types: dict[str, TypeClass]
    """Normalized column name → TypeClass for this variant."""
    member_indices: list[int] = field(default_factory=list)


@dataclass
class TypeInferenceResult:
    """Output of Tier 2 for the full fileset."""
    file_samples: dict[int, FileSample]
    """file_index → FileSample. Missing entries = sampling failed."""
    type_variants: list[TypeVariant]
    cross_file_disagreements: list[Finding]
    """Cross-file type disagreement findings, grouped by column."""
    file_type_variant_ids: dict[int, str]
    """file_index → variant_id."""


def infer_types(
    physical_files: list[PhysicalFile],
    families: list[FamilyCandidate],
    file_family_ids: list[str | None],
    conn: duckdb.DuckDBPyConnection,
    config: SampleConfig | None = None,
) -> TypeInferenceResult:
    """
    Run type inference within each Tier-1 family.

    Operates family-by-family (not across the whole folder) per spec §10.
    """
    if config is None:
        config = SampleConfig()

    file_samples: dict[int, FileSample] = {}
    type_variants: list[TypeVariant] = []
    cross_file_disagreements: list[Finding] = []
    file_type_variant_ids: dict[int, str] = {}

    variant_counter = 0

    for family in families:
        # Collect samples for all files in this family
        family_samples: dict[int, FileSample] = {}

        for file_idx in family.member_indices:
            pf = physical_files[file_idx]
            if not pf.raw_column_names:
                continue

            if pf.column_types:
                from willitload.tier2.sampler import ColumnTypeSample
                columns_sample = [
                    ColumnTypeSample(
                        column_name=name,
                        inferred_class=tc,
                        null_count=0,
                        sample_row_count=0,
                    )
                    for name, tc in pf.column_types.items()
                ]
                sample = FileSample(
                    path=str(pf.path),
                    columns=columns_sample,
                    sample_row_count=0,
                    full_file=True,
                )
                family_samples[file_idx] = sample
                file_samples[file_idx] = sample

        if not family_samples:
            continue

        # Detect cross-file type disagreements within this family
        disagreements = _find_disagreements(
            family.family_id,
            family_samples,
            physical_files,
        )
        cross_file_disagreements.extend(disagreements)

        # Split family into type variants
        variant_key_to_id: dict[str, str] = {}
        for file_idx, sample in family_samples.items():
            type_key = _type_key(sample)
            if type_key not in variant_key_to_id:
                variant_counter += 1
                vid = f"V{variant_counter:04d}"
                variant = TypeVariant(
                    variant_id=vid,
                    family_id=family.family_id,
                    column_types=sample.column_types(),
                    member_indices=[file_idx],
                )
                type_variants.append(variant)
                variant_key_to_id[type_key] = vid
            else:
                vid = variant_key_to_id[type_key]
                next(v for v in type_variants if v.variant_id == vid).member_indices.append(file_idx)

            file_type_variant_ids[file_idx] = vid

    return TypeInferenceResult(
        file_samples=file_samples,
        type_variants=type_variants,
        cross_file_disagreements=cross_file_disagreements,
        file_type_variant_ids=file_type_variant_ids,
    )


def _sample_file(
    pf: PhysicalFile,
    conn: duckdb.DuckDBPyConnection,
    config: SampleConfig,
) -> FileSample | None:
    """Dispatch sampling to the appropriate sampler based on format."""
    fmt = pf.format_detected or ""

    match fmt:
        case "csv" | "tsv":
            return sample_csv_types(
                path=pf.path,
                normalized_names=pf.normalized_column_names if hasattr(pf, "normalized_column_names") else pf.raw_column_names,
                raw_names=pf.raw_column_names,
                encoding=pf.encoding or "utf-8",
                delimiter=pf.delimiter or ",",
                has_header=bool(pf.has_header),
                conn=conn,
                config=config,
            )
        case "parquet":
            return sample_parquet_types(
                path=pf.path,
                normalized_names=pf.raw_column_names,
                conn=conn,
            )
        case _:
            return None


def _type_key(sample: FileSample) -> str:
    """Create a hashable key representing the full type profile of a sample."""
    return "|".join(
        f"{c.column_name}:{c.inferred_class.value}"
        for c in sorted(sample.columns, key=lambda c: c.column_name)
    )


def _find_disagreements(
    family_id: str,
    family_samples: dict[int, FileSample],
    physical_files: list[PhysicalFile],
) -> list[Finding]:
    """
    Find columns where type inference disagrees across files in the same family.

    e.g. "id: int in 40 files, text in 3 files" — an elevated first-class finding
    because it bites especially hard in sequential/pandas concat workloads.
    """
    findings: list[Finding] = []

    # Collect per-column type observations
    col_types: dict[str, Counter[TypeClass]] = defaultdict(Counter)
    for file_idx, sample in family_samples.items():
        for col in sample.columns:
            col_types[col.column_name][col.inferred_class] += 1

    for col_name, type_counts in col_types.items():
        if len(type_counts) <= 1:
            continue  # All files agree — no disagreement

        # Disagreement found
        total = sum(type_counts.values())
        majority_type, majority_count = type_counts.most_common(1)[0]
        minority_parts = [
            f"{tc.value} in {cnt} file(s)"
            for tc, cnt in type_counts.items()
            if tc != majority_type
        ]

        findings.append(
            Finding(
                reason_code=ReasonCode.TYPE_MISMATCH,
                severity=Severity.WARN,  # cross-file disagreement is WARN; baseline comparison may raise to ERROR
                locus=f"column '{col_name}' (family {family_id})",
                expected=f"{majority_type.value} ({majority_count}/{total} files)",
                found="; ".join(minority_parts),
                explanation=(
                    f"Column '{col_name}' infers as different types across files in family {family_id}: "
                    f"{majority_type.value} in {majority_count} files, "
                    + ", ".join(minority_parts) + ". "
                    f"This is especially dangerous for sequential concat operations (pandas, etc.) "
                    f"where type coercion can silently corrupt data."
                ),
                confidence=total,
            )
        )

    return findings
