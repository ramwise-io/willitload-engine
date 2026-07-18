"""
willitload.check -- Structural diff engine for `check` mode.

Compares each file's fingerprint against the user-supplied baseline and
produces the golden/broken partition with exact per-file deviations and
mode-appropriate severity.

The five drift forms (sec.12):
  - Additive (new column): EXTRA_COLUMN
  - Missing (dropped column): MISSING_COLUMN
  - Reorder: non-event in name mode; COUNT_CHANGED/TYPE_MISMATCH in position mode
  - Type change: TYPE_MISMATCH (widening=WARN, breaking=ERROR)
  - Rename: exposed as evidence (missing + extra at same position) -- verdict withheld

Severity is always derived from project_severity(reason_code, alignment_mode).
Never encoded into the reason code itself.

Exit-code contract:
  `check` exits non-zero if ANY verdict in `broken` has ERROR severity.
  The `has_errors` property on CheckResult captures this.
"""

from __future__ import annotations

from willitload.baseline.fingerprint import BaselineFingerprint
from willitload.models import (
    AlignmentMode,
    Bucket,
    CheckResult,
    ExtraColumnPolicy,
    FileVerdict,
    Finding,
    ReasonCode,
    Severity,
    Verdict,
    Accounting,
    project_severity,
)
from willitload.types import TypeClass, check_compatibility, Compatibility


# ---------------------------------------------------------------------------
# Main diff function
# ---------------------------------------------------------------------------

def diff_file_against_baseline(
    verdict: FileVerdict,
    baseline: BaselineFingerprint,
    mode: AlignmentMode,
    extra_policy: ExtraColumnPolicy,
) -> FileVerdict:
    """
    Diff a FileVerdict's structural fingerprint against a baseline.

    Mutates `verdict.findings` and `verdict.verdict` in place.
    Returns the modified verdict for chaining.

    The impossible quadrant (headerless + name mode) -> HEADERLESS_NAME_MODE finding.
    """
    findings: list[Finding] = []

    # --- Impossible quadrant check ---
    if mode == AlignmentMode.NAME and verdict.has_header is False:
        findings.append(
            Finding(
                reason_code=ReasonCode.HEADERLESS_NAME_MODE,
                severity=project_severity(ReasonCode.HEADERLESS_NAME_MODE, mode),
                locus="file header",
                expected="header row present (name mode requires names)",
                found="no header detected",
                explanation=(
                    "Name-mode alignment requires named columns, but this file has no header. "
                    "Cannot bind by name -- reported as anomaly, not silently switched to position mode. "
                    "Use --align position or add a header to this file."
                ),
            )
        )
        verdict.findings = findings
        verdict.verdict = Verdict.BROKEN
        verdict.explanation = "Headerless file in name-mode: impossible quadrant (sec.5.3)."
        return verdict

    # --- Dispatch by alignment mode ---
    if mode == AlignmentMode.NAME:
        findings.extend(_diff_name_mode(verdict, baseline, extra_policy))
    else:
        findings.extend(_diff_position_mode(verdict, baseline, extra_policy))

    verdict.findings = findings

    # Determine verdict: BROKEN if any ERROR finding, CONFORMS otherwise
    has_errors = any(f.severity == Severity.ERROR for f in findings)
    verdict.verdict = Verdict.BROKEN if has_errors else Verdict.CONFORMS

    if verdict.verdict == Verdict.CONFORMS and findings:
        verdict.explanation = (
            "Conforms structurally to the declared contract -- "
            "warnings present but no load-breaking deviations. "
            "Note: 'Conforms' means structural match only. "
            "Whether values under a column mean what the column claims is not structurally verifiable."
        )
    elif verdict.verdict == Verdict.CONFORMS:
        verdict.explanation = (
            "Conforms structurally to the declared contract. "
            "Note: 'Conforms' means structural match only. "
            "Whether values under a column mean what the column claims is not structurally verifiable."
        )

    return verdict


# ---------------------------------------------------------------------------
# Name-mode diff
# ---------------------------------------------------------------------------

def _diff_name_mode(
    verdict: FileVerdict,
    baseline: BaselineFingerprint,
    extra_policy: ExtraColumnPolicy,
) -> list[Finding]:
    """
    Name-mode structural diff.

    Checks: expected columns present, names match (after canonicalization),
    types compatible. Order is irrelevant (name mode is order-independent).
    """
    findings: list[Finding] = []
    mode = AlignmentMode.NAME

    file_names = frozenset(verdict.normalized_column_names)
    baseline_names = baseline.name_set
    baseline_type_map = baseline.name_to_type

    # Missing columns (expected but absent)
    missing = sorted(baseline_names - file_names)
    for name in missing:
        findings.append(
            Finding(
                reason_code=ReasonCode.MISSING_COLUMN,
                severity=project_severity(ReasonCode.MISSING_COLUMN, mode),
                locus=f"column '{name}'",
                expected=name,
                found=None,
                explanation=(
                    f"Column '{name}' declared in baseline is absent from this file. "
                    f"A name-bound load will fail to find this column and break/error."
                ),
            )
        )

    # Extra columns (present in file but not declared)
    extra = sorted(file_names - baseline_names)
    for name in extra:
        extra_severity = _extra_severity(extra_policy, mode)
        findings.append(
            Finding(
                reason_code=ReasonCode.EXTRA_COLUMN,
                severity=extra_severity,
                locus=f"column '{name}'",
                expected=None,
                found=name,
                explanation=(
                    f"Column '{name}' is present in this file but not declared in the baseline. "
                    + (
                        "With --extra strict, this is treated as drift."
                        if extra_policy == ExtraColumnPolicy.STRICT
                        else "With --extra open, extra columns are allowed."
                    )
                ),
            )
        )

    # Type compatibility (for columns that are present in both)
    common = file_names & baseline_names
    file_type_map = verdict.column_types

    for name in sorted(common):
        baseline_type = baseline_type_map.get(name)
        file_type_raw = file_type_map.get(name)
        if baseline_type is None or file_type_raw is None:
            continue

        from willitload.types import normalize_type
        file_type = normalize_type(file_type_raw) if isinstance(file_type_raw, str) else TypeClass(file_type_raw)

        compat = check_compatibility(baseline_type, file_type)
        if compat == Compatibility.IDENTICAL:
            continue

        severity = (
            Severity.WARN if compat == Compatibility.WIDENING
            else project_severity(ReasonCode.TYPE_MISMATCH, mode)
        )
        findings.append(
            Finding(
                reason_code=ReasonCode.TYPE_MISMATCH,
                severity=severity,
                locus=f"column '{name}'",
                expected=baseline_type.value,
                found=file_type.value,
                explanation=(
                    f"Column '{name}': baseline declares {baseline_type.value}, "
                    f"file infers {file_type.value}. "
                    + (
                        "Widening type change -- structurally compatible but may indicate upstream change."
                        if compat == Compatibility.WIDENING
                        else "Breaking type change -- the load may fail or silently corrupt this column."
                    )
                ),
            )
        )

    # Rename evidence (name mode: missing+extra at same position is a rename candidate)
    _add_rename_evidence_name_mode(missing, extra, verdict, baseline, findings)

    return findings


def _add_rename_evidence_name_mode(
    missing: list[str],
    extra: list[str],
    verdict: FileVerdict,
    baseline: BaselineFingerprint,
    findings: list[Finding],
) -> None:
    """
    Surface rename evidence as observations without asserting a rename verdict.

    Per sec.12 and sec.13: the tool exposes the evidence (expected vs found name at a
    position, same type) and WITHHOLDS the verdict. User's eye decides.
    No content inference used.
    """
    if not missing or not extra:
        return

    baseline_ordered = baseline.ordered_names
    file_ordered = verdict.normalized_column_names

    # For each (missing, extra) pair at the same position, surface as evidence
    baseline_positions = {name: i for i, name in enumerate(baseline_ordered)}
    file_positions = {name: i for i, name in enumerate(file_ordered)}

    for missing_name in missing:
        b_pos = baseline_positions.get(missing_name)
        if b_pos is None:
            continue
        for extra_name in extra:
            f_pos = file_positions.get(extra_name)
            if f_pos == b_pos:
                # Same position, different name -> rename candidate
                findings.append(
                    Finding(
                        reason_code=ReasonCode.COLUMN_NAME_MISMATCH,
                        severity=Severity.WARN,  # Evidence only -- not asserting rename
                        locus=f"position {b_pos}",
                        expected=missing_name,
                        found=extra_name,
                        explanation=(
                            f"Position {b_pos}: baseline expects '{missing_name}', "
                            f"file has '{extra_name}'. "
                            f"This may be a rename. The tool surfaces this as evidence -- "
                            f"rename identity is the user's declaration, not the tool's inference (see spec section 13)."
                        ),
                        confidence=1,
                    )
                )
                break  # one rename candidate per missing name


# ---------------------------------------------------------------------------
# Position-mode diff
# ---------------------------------------------------------------------------

def _diff_position_mode(
    verdict: FileVerdict,
    baseline: BaselineFingerprint,
    extra_policy: ExtraColumnPolicy,
) -> list[Finding]:
    """
    Position-mode structural diff.

    Checks: column count matches; type-at-each-position matches.
    If baseline carries names AND file has names -> also compare name-at-position (WARN).
    """
    findings: list[Finding] = []
    mode = AlignmentMode.POSITION

    file_count = verdict.column_count or 0
    baseline_count = baseline.column_count

    # Column count check
    if file_count != baseline_count:
        severity = project_severity(ReasonCode.COUNT_CHANGED, mode)
        findings.append(
            Finding(
                reason_code=ReasonCode.COUNT_CHANGED,
                severity=severity,
                locus="column count",
                expected=str(baseline_count),
                found=str(file_count),
                explanation=(
                    f"File has {file_count} columns; baseline declares {baseline_count}. "
                    f"A position-bound load will misalign every column beyond position "
                    f"{min(file_count, baseline_count)}."
                ),
            )
        )
        # Still check shared positions for type drift
        check_count = min(file_count, baseline_count)
    else:
        check_count = baseline_count

    # Type-at-position check
    baseline_types = baseline.ordered_types
    file_types_raw = list(verdict.column_types.values()) if verdict.column_types else []

    from willitload.types import normalize_type
    file_type_list = [
        normalize_type(t) if isinstance(t, str) else TypeClass(t)
        for t in file_types_raw
    ]

    for pos in range(check_count):
        if pos >= len(baseline_types) or pos >= len(file_type_list):
            break

        baseline_type = baseline_types[pos]
        file_type = file_type_list[pos]

        compat = check_compatibility(baseline_type, file_type)
        if compat == Compatibility.IDENTICAL:
            continue

        severity = (
            Severity.WARN if compat == Compatibility.WIDENING
            else project_severity(ReasonCode.TYPE_MISMATCH, mode)
        )
        findings.append(
            Finding(
                reason_code=ReasonCode.TYPE_MISMATCH,
                severity=severity,
                locus=f"position {pos}",
                expected=baseline_type.value,
                found=file_type.value,
                explanation=(
                    f"Position {pos}: baseline declares {baseline_type.value}, "
                    f"file infers {file_type.value}. "
                    + (
                        "Widening -- structurally compatible but worth checking."
                        if compat == Compatibility.WIDENING
                        else "Breaking -- the load may fail or corrupt this column."
                    )
                ),
            )
        )

    # Name-at-position comparison (if both baseline and file have names)
    baseline_names = baseline.ordered_names
    file_names = verdict.normalized_column_names

    if baseline_names and file_names:
        for pos in range(min(len(baseline_names), len(file_names), check_count)):
            b_name = baseline_names[pos]
            f_name = file_names[pos] if pos < len(file_names) else None
            if f_name and b_name != f_name:
                # Name mismatch in position mode -> WARN (swap/rename hint; load tolerates it)
                findings.append(
                    Finding(
                        reason_code=ReasonCode.COLUMN_NAME_MISMATCH,
                        severity=project_severity(ReasonCode.COLUMN_NAME_MISMATCH, mode),
                        locus=f"position {pos}",
                        expected=b_name,
                        found=f_name,
                        explanation=(
                            f"Position {pos} is named '{f_name}' in this file; "
                            f"baseline expects '{b_name}'. "
                            f"The positional load is unaffected (it binds by position, not name), "
                            f"but this may indicate a column swap that the load cannot detect. "
                            f"(sec.5.4: name mismatch in position mode -> WARN)"
                        ),
                    )
                )
    elif file_names and not baseline_names:
        # sec.5.5: Surface header names as observations (no verdict)
        # Already in the verdict's normalized_column_names -- no additional findings needed
        pass

    return findings


# ---------------------------------------------------------------------------
# Extra-column severity helper
# ---------------------------------------------------------------------------

def _extra_severity(policy: ExtraColumnPolicy, mode: AlignmentMode) -> Severity:
    """Severity for EXTRA_COLUMN based on the extra-column policy CLI flag."""
    match policy:
        case ExtraColumnPolicy.STRICT:
            return project_severity(ReasonCode.EXTRA_COLUMN, mode)
        case ExtraColumnPolicy.OPEN:
            return Severity.INFO
        case _:
            return Severity.WARN


# ---------------------------------------------------------------------------
# Partition builder
# ---------------------------------------------------------------------------

def partition_results(
    verdicts: list[FileVerdict],
) -> tuple[list[FileVerdict], list[FileVerdict], list[FileVerdict]]:
    """
    Partition verdicts into (golden, warned, broken).

    golden: CONFORMS with no findings at all
    warned: CONFORMS but has WARN-level findings
    broken: BROKEN (has at least one ERROR finding)
    """
    golden: list[FileVerdict] = []
    warned: list[FileVerdict] = []
    broken: list[FileVerdict] = []

    for v in verdicts:
        if v.verdict == Verdict.BROKEN:
            broken.append(v)
        elif v.findings:
            warned.append(v)
        else:
            golden.append(v)

    return golden, warned, broken
