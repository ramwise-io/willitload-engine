"""
willitload.core — The headless engine. No I/O, no printing.

Exposes scan() and check() as pure functions.
Called by the CLI (thin renderer) and importable directly as a library:

    from willitload import scan, check
    result = scan("./data/**/*.csv")
    if result.accounting.refused > 0:
        print("Some files were unreadable")
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb

from willitload.baseline.fingerprint import BaselineFingerprint
from willitload.models import (
    Accounting,
    AlignmentMode,
    Bucket,
    CheckResult,
    ExtraColumnPolicy,
    FamilySummary,
    FileVerdict,
    Finding,
    ReasonCode,
    ScanResult,
    Severity,
    Verdict,
)
from willitload.tier0.resolver import ResolverConfig, ResolverResult, resolve
from willitload.tier0.physical import PhysicalFile
from willitload.tier1.canonicalize import canonicalize_names, CanonicalizationConfig
from willitload.tier1.cluster import assign_families, FamilyCandidate
from willitload.tier1.anomalies import detect_csv_anomalies
from willitload.tier2.infer import infer_types
from willitload.check import diff_file_against_baseline, partition_results


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------

def scan(
    path_expr: str,
    config: ResolverConfig | None = None,
) -> ScanResult:
    """
    Describe a fileset's structure: families, variants, within-set outliers.

    Parameters:
        path_expr: directory path, glob pattern, or single file path.
        config: scale bounds and access settings.

    Returns:
        ScanResult — the typed result object. Use .to_json() for JSON output.
    """
    t0 = time.monotonic()

    # --- Tier 0: Physical resolution ---
    resolver_result = resolve(path_expr, config)
    physical_files = resolver_result.physical_files
    set_findings = list(resolver_result.set_findings)

    if not physical_files:
        elapsed = (time.monotonic() - t0) * 1000
        return ScanResult(
            path_expression=path_expr,
            elapsed_ms=elapsed,
            accounting=Accounting(0, 0, 0, 0, 0),
            file_verdicts=[],
            scan_findings=set_findings,
        )

    # --- Tier 1: Header clustering ---
    conn = duckdb.connect(":memory:")
    conn.execute("SET enable_progress_bar = false")

    try:
        # Canonicalize column names
        norm_config = CanonicalizationConfig()
        all_normalized: list[list[str]] = []
        for pf in physical_files:
            normalized, traces = canonicalize_names(pf.raw_column_names or [], norm_config)
            pf.normalized_column_names = normalized
            all_normalized.append(normalized)

        # Assign families
        cluster_result = assign_families(all_normalized)

        # Detect intra-file anomalies for CSV files
        anomaly_findings_per_file: list[list[Finding]] = [[] for _ in physical_files]
        for i, pf in enumerate(physical_files):
            if pf.format_detected in ("csv", "tsv") and pf.is_inspectable:
                try:
                    anom = detect_csv_anomalies(pf, conn)
                    anomaly_findings_per_file[i] = anom.findings
                except Exception:
                    pass

        # --- Tier 2: Type refinement ---
        type_result = infer_types(
            physical_files=physical_files,
            families=cluster_result.families,
            file_family_ids=cluster_result.file_family_ids,
            conn=conn,
        )

    finally:
        conn.close()

    # --- Assemble FileVerdicts ---
    file_verdicts: list[FileVerdict] = []
    for i, pf in enumerate(physical_files):
        sample = type_result.file_samples.get(i)
        col_types: dict[str, str] = {}
        if sample:
            col_types = {c.column_name: c.inferred_class.value for c in sample.columns}

        fv = FileVerdict(
            path=str(pf.path),
            bucket=pf.bucket,
            verdict=Verdict.CONFORMS,  # scan mode: no baseline to fail against
            findings=list(anomaly_findings_per_file[i]),
            format_detected=pf.format_detected,
            encoding=pf.encoding,
            delimiter=pf.delimiter,
            has_header=pf.has_header,
            column_count=pf.column_count,
            size_bytes=pf.size_bytes,
            family_id=cluster_result.file_family_ids[i],
            raw_column_names=list(pf.raw_column_names or []),
            normalized_column_names=list(getattr(pf, "normalized_column_names", pf.raw_column_names or [])),
            column_types=col_types,
            type_variant_id=type_result.file_type_variant_ids.get(i),
            explanation=cluster_result.file_explanations[i],
        )

        # Encoding fallback finding
        if pf.encoding_is_fallback:
            from willitload.models import project_severity
            fv.findings.append(
                Finding(
                    reason_code=ReasonCode.ENCODING_FALLBACK,
                    severity=project_severity(ReasonCode.ENCODING_FALLBACK, None),
                    locus="file encoding",
                    expected="UTF-8, UTF-16, or explicit encoding",
                    found="latin-1 (fallback)",
                    explanation=(
                        "File only decoded successfully under Latin-1. "
                        "This is a deterministic finding — no encoding guessing was used. "
                        "Structural profile may be incomplete if encoding is incorrect."
                    ),
                )
            )

        # Physical bucket failure findings
        if pf.bucket == Bucket.REFUSED:
            if pf.error and pf.error.startswith("Permission denied"):
                fv.findings.append(
                    Finding(
                        reason_code=ReasonCode.PERMISSION_DENIED,
                        severity=Severity.ERROR,
                        locus="file access",
                        expected="read permission",
                        found="permission denied",
                        explanation=pf.error,
                    )
                )
            elif pf.error and pf.error.startswith("DECODE_ERROR:"):
                fv.findings.append(
                    Finding(
                        reason_code=ReasonCode.DECODE_ERROR,
                        severity=Severity.ERROR,
                        locus="file decoding",
                        expected=f"valid {pf.encoding} encoding",
                        found="corrupted byte sequence",
                        explanation=pf.error.split(":", 1)[1].strip(),
                    )
                )
            elif pf.error and pf.error.startswith("CORRUPT_ARCHIVE:"):
                fv.findings.append(
                    Finding(
                        reason_code=ReasonCode.CORRUPT_ARCHIVE,
                        severity=Severity.ERROR,
                        locus="file compression",
                        expected="healthy gzip archive",
                        found="corrupted gzip archive",
                        explanation=pf.error.split(":", 1)[1].strip(),
                    )
                )
            else:
                fv.findings.append(
                    Finding(
                        reason_code=ReasonCode.PERMISSION_DENIED,
                        severity=Severity.ERROR,
                        locus="file load",
                        expected="valid readable file",
                        found="unreadable file",
                        explanation=pf.error or "Unknown error loading file",
                    )
                )

        # Mark broken if anomaly errors exist
        if any(f.severity == Severity.ERROR for f in fv.findings):
            fv.verdict = Verdict.BROKEN

        file_verdicts.append(fv)

    # Add cross-file type disagreement findings to set_findings
    set_findings.extend(type_result.cross_file_disagreements)

    # Build family summaries
    families = _build_family_summaries(cluster_result.families, physical_files, type_result)

    # Accounting
    accounting = _build_accounting(physical_files)

    elapsed = (time.monotonic() - t0) * 1000

    return ScanResult(
        path_expression=path_expr,
        elapsed_ms=elapsed,
        accounting=accounting,
        file_verdicts=file_verdicts,
        families=families,
        scan_findings=set_findings,
    )


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------

def check(
    path_expr: str,
    baseline: BaselineFingerprint,
    mode: AlignmentMode = AlignmentMode.NAME,
    extra_policy: ExtraColumnPolicy = ExtraColumnPolicy.STRICT,
    config: ResolverConfig | None = None,
) -> CheckResult:
    """
    Diff each file's fingerprint against the baseline; produce the golden/broken partition.

    Parameters:
        path_expr: directory path, glob pattern, or single file path.
        baseline: a BaselineFingerprint from any of the three front-doors.
        mode: AlignmentMode.NAME or AlignmentMode.POSITION.
        extra_policy: ExtraColumnPolicy.STRICT or OPEN.
        config: scale bounds and access settings.

    Returns:
        CheckResult — the typed result object.
        Exit code: non-zero if result.has_errors is True.
    """
    t0 = time.monotonic()

    # Run the full scan pipeline first
    scan_result = scan(path_expr, config)

    # Apply baseline diff to each file verdict
    diffed_verdicts: list[FileVerdict] = []
    for fv in scan_result.file_verdicts:
        if fv.bucket in (Bucket.REFUSED, Bucket.CATALOGUED):
            # Cannot diff unreadable or unrecognized files
            diffed_verdicts.append(fv)
            continue
        diffed = diff_file_against_baseline(fv, baseline, mode, extra_policy)
        diffed_verdicts.append(diffed)

    golden, warned, broken = partition_results(diffed_verdicts)

    elapsed = (time.monotonic() - t0) * 1000

    return CheckResult(
        path_expression=path_expr,
        baseline_source=baseline.source_description,
        alignment_mode=mode,
        extra_column_policy=extra_policy,
        elapsed_ms=elapsed,
        accounting=scan_result.accounting,
        golden=golden,
        broken=broken,
        warned=warned,
        scan_findings=scan_result.scan_findings,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_accounting(physical_files: list[PhysicalFile]) -> Accounting:
    counts = {b: 0 for b in Bucket}
    for pf in physical_files:
        counts[pf.bucket] += 1
    return Accounting(
        files_seen=len(physical_files),
        profiled=counts[Bucket.PROFILED],
        degraded=counts[Bucket.DEGRADED],
        catalogued=counts[Bucket.CATALOGUED],
        refused=counts[Bucket.REFUSED],
    )


def _build_family_summaries(
    families: list[FamilyCandidate],
    physical_files: list[PhysicalFile],
    type_result,
) -> list[FamilySummary]:
    summaries: list[FamilySummary] = []
    for family in families:
        # Count type variants in this family
        variant_ids = set()
        for idx in family.member_indices:
            vid = type_result.file_type_variant_ids.get(idx)
            if vid:
                variant_ids.add(vid)

        summaries.append(
            FamilySummary(
                family_id=family.family_id,
                file_count=len(family.member_indices),
                representative_columns=list(family.canonical_column_order),
                column_count=family.column_count,
                type_variants=len(variant_ids) if variant_ids else 1,
            )
        )
    return summaries
