"""
willitload.tier0.physical — Physical file description (pre-profiling struct).

This intermediate data class carries everything Tier 0 discovers about a file
before Tier 1/2 operate on it. It is NOT part of the public API — only the
FileVerdict objects (built from PhysicalFile by the assembler) are public.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from willitload.models import Bucket
from willitload.types import TypeClass


@dataclass
class PhysicalFile:
    """
    Physical properties of a single file as determined by Tier 0.

    Populated incrementally as the resolver runs its detection pipeline.
    Partial population is allowed for degraded/refused files.
    """
    path: Path
    size_bytes: int

    bucket: Bucket = Bucket.CATALOGUED

    # Format detection
    format_detected: str | None = None
    """Lower-case short name: 'csv', 'parquet', 'json', 'jsonl', 'sqlite',
       'xml', 'zip', 'gzip', 'excel', 'fixed-width', 'unknown'."""
    format_confidence: int = 0
    """Number of agreeing signals (magic bytes, extension, content sample)."""

    # Encoding detection (deterministic — no chardet)
    encoding: str | None = None
    """Encoding that successfully decoded the file: 'utf-8', 'utf-16-le',
       'utf-16-be', 'utf-32-le', 'utf-32-be', 'latin-1'."""
    encoding_is_fallback: bool = False
    """True if Latin-1 was the only successful decoding (ENCODING_FALLBACK finding)."""

    # CSV-specific physical properties
    delimiter: str | None = None
    quote_char: str | None = None
    escape_char: str | None = None
    newline: str | None = None
    """'\\n', '\\r\\n', or '\\r'."""

    # Structure discovered
    has_header: bool | None = None
    """None = not yet determined; True/False = detected."""
    column_count: int | None = None
    row_count_estimate: int | None = None

    # Column names (raw, before canonicalization)
    raw_column_names: list[str] = field(default_factory=list)
    # Column names after canonicalization (set by Tier 1)
    normalized_column_names: list[str] = field(default_factory=list)
    # Column types inferred from data sample (set by worker thread)
    column_types: dict[str, TypeClass] = field(default_factory=dict)

    # Archive membership
    container_path: str | None = None
    """Path of the containing archive, if this file was extracted from one."""
    archive_depth: int = 0

    # Error capture (partial files, permission issues)
    error: str | None = None
    """Short human-readable description of any acquisition error."""

    @property
    def is_inspectable(self) -> bool:
        return self.bucket in (Bucket.PROFILED, Bucket.DEGRADED)

    @property
    def relative_path_str(self) -> str:
        return str(self.path)
