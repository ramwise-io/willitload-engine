"""
willitload.models — Typed result objects and the finding schema.

This module defines the entire output contract.
- The JSON emitted by `--json` is a direct serialization of these objects.
- The CLI's rich renderer formats ONLY what is already present in these objects.
- Severity is always derived via the projection table (reason_code, alignment_mode) → Severity.
  It is NEVER encoded into the ReasonCode name itself.

Design rules (non-negotiable):
  1. One ReasonCode per finding type.
  2. Severity = project(reason_code, alignment_mode) — see SEVERITY_PROJECTION.
  3. The renderer never derives new information; all display-needed values live here.
  4. JSON shape is the API; do not break it in minor bumps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ReasonCode(str, Enum):
    """
    Stable, machine-readable reason codes.
    One code per finding type. Severity is a projection of (code, mode).
    """

    # --- Column-level structural drift ---
    MISSING_COLUMN = "MISSING_COLUMN"
    """A column expected by the baseline is absent from the file."""

    EXTRA_COLUMN = "EXTRA_COLUMN"
    """A column present in the file was not declared in the baseline."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    """A column's inferred type is incompatible with the baseline type."""

    COLUMN_NAME_MISMATCH = "COLUMN_NAME_MISMATCH"
    """
    A column name at a position differs from the baseline expectation.
    In name-mode: ERROR (the name-bound load will break/mismap).
    In position-mode: WARN (the positional load tolerates it, but flags a possible swap).
    """

    COUNT_CHANGED = "COUNT_CHANGED"
    """Column count differs from baseline (position mode primary signal)."""

    # --- Intra-file structural anomalies ---
    RAGGED_ROWS = "RAGGED_ROWS"
    """One or more rows have a column count that deviates from the file's mode."""

    MULTI_RECORD = "MULTI_RECORD"
    """Two or more distinct stable column-count regimes detected in the file."""

    TRUNCATED = "TRUNCATED"
    """Abrupt EOF mid-record or final partial row detected."""

    TRAILING_SUMMARY = "TRAILING_SUMMARY"
    """Final row(s) break the column pattern (likely a totals/summary row)."""

    # --- Physical / acquisition findings ---
    ENCODING_FALLBACK = "ENCODING_FALLBACK"
    """
    File only decoded successfully under Latin-1 fallback (not UTF-8/UTF-16).
    Deterministic finding — no probabilistic guessing involved.
    """

    DECODE_ERROR = "DECODE_ERROR"
    """File content contains byte sequences that cannot be decoded under the expected encoding."""

    CORRUPT_ARCHIVE = "CORRUPT_ARCHIVE"
    """The compressed archive is corrupted, truncated, or unreadable."""

    FORMAT_UNRECOGNIZED = "FORMAT_UNRECOGNIZED"
    """File format could not be identified from magic bytes or content sampling."""

    NESTED_ARCHIVE = "NESTED_ARCHIVE"
    """Archive nesting exceeds the configured scan depth."""

    ENCRYPTED_ARCHIVE = "ENCRYPTED_ARCHIVE"
    """Archive is password-protected; contents not inspected."""

    PERMISSION_DENIED = "PERMISSION_DENIED"
    """File could not be opened due to OS permissions."""

    # --- Alignment / mode violations ---
    HEADERLESS_NAME_MODE = "HEADERLESS_NAME_MODE"
    """
    A headerless file was encountered under name-mode alignment.
    Impossible quadrant (§5.3): reported anomaly, never silently switched to position mode.
    """

    # --- Scale / ceiling findings ---
    FILE_COUNT_CEILING = "FILE_COUNT_CEILING"
    """Expanded file count exceeded the configured ceiling."""

    BYTE_CEILING = "BYTE_CEILING"
    """Total bytes scanned exceeded the configured ceiling."""

    RECURSION_DEPTH_CEILING = "RECURSION_DEPTH_CEILING"
    """Directory recursion exceeded the configured depth ceiling."""


class Severity(str, Enum):
    """Mode-appropriate severity. Derived via SEVERITY_PROJECTION; never stored in ReasonCode."""
    ERROR = "ERROR"
    """The load will actually break or corrupt on this finding."""
    WARN = "WARN"
    """The load tolerates it, but it hints at a problem the load cannot see."""
    INFO = "INFO"
    """Informational; no load impact expected."""


class AlignmentMode(str, Enum):
    NAME = "name"
    POSITION = "position"


class ExtraColumnPolicy(str, Enum):
    STRICT = "strict"   # any extra column is ERROR/WARN drift
    OPEN = "open"       # extras are INFO; only declared columns checked


class Bucket(str, Enum):
    """Exactly one bucket per file. Files_seen = sum of all buckets."""
    PROFILED = "profiled"
    """Recognized and fully fingerprinted."""
    DEGRADED = "degraded"
    """Partially readable; structural profile may be incomplete."""
    CATALOGUED = "catalogued"
    """Physical properties known, not structurally profiled (unrecognized format)."""
    REFUSED = "refused"
    """Permission-denied, corrupt, or encrypted; not inspectable."""


class Verdict(str, Enum):
    CONFORMS = "conforms"
    BROKEN = "broken"


# ---------------------------------------------------------------------------
# Severity projection table (the single rule from §5.4)
#
# Severity = does the mismatched attribute match what the declared load binds on?
#   - Mismatched attribute IS load-binding → ERROR
#   - Mismatched attribute is non-binding but suggestive → WARN
#
# This table is the ONLY place severity is computed.
# ReasonCode names are stable across modes; only severity changes.
# ---------------------------------------------------------------------------

SEVERITY_PROJECTION: dict[tuple[ReasonCode, AlignmentMode | None], Severity] = {
    # Column-level drift
    (ReasonCode.MISSING_COLUMN,       AlignmentMode.NAME):     Severity.ERROR,
    (ReasonCode.MISSING_COLUMN,       AlignmentMode.POSITION): Severity.ERROR,
    (ReasonCode.EXTRA_COLUMN,         AlignmentMode.NAME):     Severity.ERROR,   # overridden by policy flag
    (ReasonCode.EXTRA_COLUMN,         AlignmentMode.POSITION): Severity.ERROR,   # overridden by policy flag
    (ReasonCode.TYPE_MISMATCH,        AlignmentMode.NAME):     Severity.ERROR,   # breaking; WARN for widening
    (ReasonCode.TYPE_MISMATCH,        AlignmentMode.POSITION): Severity.ERROR,   # breaking; WARN for widening
    (ReasonCode.COLUMN_NAME_MISMATCH, AlignmentMode.NAME):     Severity.ERROR,   # name is what binds
    (ReasonCode.COLUMN_NAME_MISMATCH, AlignmentMode.POSITION): Severity.WARN,    # position binds; name mismatch is a swap hint
    (ReasonCode.COUNT_CHANGED,        AlignmentMode.POSITION): Severity.ERROR,
    (ReasonCode.COUNT_CHANGED,        AlignmentMode.NAME):     Severity.INFO,    # name mode doesn't bind on count

    # Intra-file anomalies (mode-independent)
    (ReasonCode.RAGGED_ROWS,          None): Severity.ERROR,
    (ReasonCode.MULTI_RECORD,         None): Severity.WARN,
    (ReasonCode.TRUNCATED,            None): Severity.ERROR,
    (ReasonCode.TRAILING_SUMMARY,     None): Severity.WARN,

    # Physical findings (mode-independent)
    (ReasonCode.ENCODING_FALLBACK,    None): Severity.WARN,
    (ReasonCode.FORMAT_UNRECOGNIZED,  None): Severity.INFO,
    (ReasonCode.NESTED_ARCHIVE,       None): Severity.WARN,
    (ReasonCode.ENCRYPTED_ARCHIVE,    None): Severity.WARN,
    (ReasonCode.PERMISSION_DENIED,    None): Severity.ERROR,
    (ReasonCode.DECODE_ERROR,         None): Severity.ERROR,
    (ReasonCode.CORRUPT_ARCHIVE,      None): Severity.ERROR,

    # Mode violations
    (ReasonCode.HEADERLESS_NAME_MODE, None): Severity.ERROR,

    # Scale ceilings
    (ReasonCode.FILE_COUNT_CEILING,   None): Severity.WARN,
    (ReasonCode.BYTE_CEILING,         None): Severity.WARN,
    (ReasonCode.RECURSION_DEPTH_CEILING, None): Severity.WARN,
}


def project_severity(
    code: ReasonCode,
    mode: AlignmentMode | None,
) -> Severity:
    """
    Look up severity from the projection table.
    Mode-independent findings use None as the mode key.
    Falls back to mode=None key if the specific mode key is absent.
    """
    key = (code, mode)
    if key in SEVERITY_PROJECTION:
        return SEVERITY_PROJECTION[key]
    key_none = (code, None)
    if key_none in SEVERITY_PROJECTION:
        return SEVERITY_PROJECTION[key_none]
    # Safe default — should not be reached if projection table is complete
    assert False, f"Missing severity projection for {code} under {mode}"
    return Severity.WARN


# ---------------------------------------------------------------------------
# Data objects
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Finding:
    """
    A single structural deviation within a file.

    All display-needed values must live here.
    The renderer formats this; it never derives new information from it.
    """
    reason_code: ReasonCode
    severity: Severity
    locus: str
    """Human-readable location: column name, position index, or 'file-level'."""
    expected: str | None
    """What the baseline or population declared (None if not applicable)."""
    found: str | None
    """What was actually observed (None if not applicable)."""
    explanation: str
    """Human-readable, self-contained explanation. Carries the 'why'."""
    confidence: int = 1
    """Count of independent agreeing signals (honest, not a probability)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code.value,
            "severity": self.severity.value,
            "locus": self.locus,
            "expected": self.expected,
            "found": self.found,
            "explanation": self.explanation,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class FileVerdict:
    """
    The atomic unit of output: one file, one verdict, zero or more findings.

    Accounts for every file — nothing is silently dropped.
    Bucket assignment ensures the accounting reconciliation always holds.
    """
    path: str
    """Absolute path to the file."""
    bucket: Bucket
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)

    # Physical properties (from Tier 0) — always populated for profiled files
    format_detected: str | None = None
    """e.g. 'csv', 'parquet', 'json', 'jsonl', 'sqlite', 'xml', 'zip'"""
    encoding: str | None = None
    delimiter: str | None = None
    has_header: bool | None = None
    column_count: int | None = None
    row_count_estimate: int | None = None
    size_bytes: int | None = None

    # Tier 1 — populated after clustering
    family_id: str | None = None
    """Opaque ID of the structural family this file belongs to."""
    raw_column_names: list[str] = field(default_factory=list)
    normalized_column_names: list[str] = field(default_factory=list)

    # Tier 2 — populated after type refinement
    column_types: dict[str, str] = field(default_factory=dict)
    """Mapping of normalized column name → TypeClass string."""
    type_variant_id: str | None = None

    explanation: str = ""
    """Per-file explanation of its placement or verdict. Required for every broken file."""

    @property
    def conforms(self) -> bool:
        return self.verdict == Verdict.CONFORMS

    @property
    def has_errors(self) -> bool:
        return any(f.severity == Severity.ERROR for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "bucket": self.bucket.value,
            "verdict": self.verdict.value,
            "findings": [f.to_dict() for f in self.findings],
            "format_detected": self.format_detected,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "has_header": self.has_header,
            "column_count": self.column_count,
            "row_count_estimate": self.row_count_estimate,
            "size_bytes": self.size_bytes,
            "family_id": self.family_id,
            "raw_column_names": self.raw_column_names,
            "normalized_column_names": self.normalized_column_names,
            "column_types": self.column_types,
            "type_variant_id": self.type_variant_id,
            "explanation": self.explanation,
        }


@dataclass(slots=True)
class FamilySummary:
    """Summary of one structural family (group of files with identical normalized structure)."""
    family_id: str
    file_count: int
    representative_columns: list[str]
    """Normalized column names of the canonical member."""
    column_count: int
    type_variants: int
    """Number of distinct type profiles within this family."""
    within_family_findings: list[str] = field(default_factory=list)
    """Descriptions of within-family structural variants (header diffs, type drifts)."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Accounting:
    """
    File accounting reconciliation.
    Invariant: files_seen == profiled + degraded + catalogued + refused
    """
    files_seen: int
    profiled: int
    degraded: int
    catalogued: int
    refused: int

    def reconciles(self) -> bool:
        return self.files_seen == (
            self.profiled + self.degraded + self.catalogued + self.refused
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_seen": self.files_seen,
            "profiled": self.profiled,
            "degraded": self.degraded,
            "catalogued": self.catalogued,
            "refused": self.refused,
            "reconciles": self.reconciles(),
        }


@dataclass(slots=True)
class ScanResult:
    """
    Output of `scan` — full structural description of a fileset.

    This is the primary data structure; all views (family summaries, outliers)
    are groupings over `file_verdicts`. The renderer formats this object only;
    it never derives information not present here.
    """
    path_expression: str
    elapsed_ms: float
    accounting: Accounting
    file_verdicts: list[FileVerdict]
    families: list[FamilySummary] = field(default_factory=list)
    scan_findings: list[Finding] = field(default_factory=list)
    """File-set-level findings (e.g. ceiling warnings, zero-match)."""
    version: str = "0.1.0"

    @property
    def files_seen(self) -> int:
        return self.accounting.files_seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "path_expression": self.path_expression,
            "elapsed_ms": self.elapsed_ms,
            "accounting": self.accounting.to_dict(),
            "families": [f.to_dict() for f in self.families],
            "scan_findings": [f.to_dict() for f in self.scan_findings],
            "file_verdicts": [v.to_dict() for v in self.file_verdicts],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


@dataclass(slots=True)
class CheckResult:
    """
    Output of `check` — the golden/broken partition against a baseline.

    Exit-code contract: non-zero if any ERROR-severity finding exists anywhere.
    The renderer formats this object only; it never derives new information.
    """
    path_expression: str
    baseline_source: str
    """Human-readable description of the baseline (file path, type)."""
    alignment_mode: AlignmentMode
    extra_column_policy: ExtraColumnPolicy
    elapsed_ms: float
    accounting: Accounting
    golden: list[FileVerdict]
    """Files that conform to the baseline (load these)."""
    broken: list[FileVerdict]
    """Files with ERROR-grade findings (fix or skip these)."""
    warned: list[FileVerdict]
    """Files with WARN-grade findings but no ERRORs."""
    scan_findings: list[Finding] = field(default_factory=list)
    """File-set-level findings."""
    version: str = "0.1.0"

    @property
    def has_errors(self) -> bool:
        return len(self.broken) > 0

    @property
    def files_seen(self) -> int:
        return self.accounting.files_seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "path_expression": self.path_expression,
            "baseline_source": self.baseline_source,
            "alignment_mode": self.alignment_mode.value,
            "extra_column_policy": self.extra_column_policy.value,
            "elapsed_ms": self.elapsed_ms,
            "accounting": self.accounting.to_dict(),
            "scan_findings": [f.to_dict() for f in self.scan_findings],
            "golden": [v.to_dict() for v in self.golden],
            "warned": [v.to_dict() for v in self.warned],
            "broken": [v.to_dict() for v in self.broken],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
