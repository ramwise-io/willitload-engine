"""
willitload.tier0.resolver — Top-level Tier 0 orchestrator.

Entry point: resolve(path_expr, config) → list[PhysicalFile]

Pipeline per file:
  1. Glob expansion (DuckDB)
  2. Size & access check
  3. Encoding detection (deterministic, no chardet)
  4. Format detection (magic bytes → content disambiguation)
  5. Structural profiling (DuckDB for CSV/JSON/Parquet; specialized parsers for others)
  6. Bucket assignment
  7. Scale ceiling enforcement

Design rules:
  - Read-only, always. Never write into the scanned location.
  - Permission-denied → finding (PERMISSION_DENIED), not crash.
  - Every file lands in exactly one bucket.
  - Accounting always reconciles: files_seen = profiled + degraded + catalogued + refused.
  - Zero-match is a finding, not an exception.
"""

from __future__ import annotations

import os
import time
import concurrent.futures
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from willitload.models import Bucket, Finding, ReasonCode, Severity
from willitload.tier0.encoding import detect_encoding
from willitload.tier0.format_detect import detect_format
from willitload.tier0.physical import PhysicalFile
from willitload.tier0.duckdb_reader import expand_glob, get_file_size, profile_file
from willitload.tier0 import parsers


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ResolverConfig:
    """Scale bounds and access settings for the resolver. All limits are explicit."""

    # File count ceiling (default: 10,000 files before requiring --force)
    file_count_ceiling: int = 10_000
    # Total bytes ceiling (default: 50 GB)
    byte_ceiling: int = 50 * 1024 * 1024 * 1024
    # Maximum recursion depth (symlink-safe; follow_symlinks=False by default)
    max_recursion_depth: int = 20
    follow_symlinks: bool = False
    # Archive nesting depth
    max_archive_depth: int = parsers.MAX_ARCHIVE_DEPTH


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ResolverResult:
    physical_files: list[PhysicalFile] = field(default_factory=list)
    set_findings: list[Finding] = field(default_factory=list)
    """File-set-level findings (ceilings, zero-match, etc.)."""
    elapsed_ms: float = 0.0

    @property
    def files_seen(self) -> int:
        return len(self.physical_files)

    @property
    def accounting(self) -> dict[str, int]:
        counts: dict[str, int] = {b.value: 0 for b in Bucket}
        for pf in self.physical_files:
            counts[pf.bucket.value] += 1
        return counts


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def resolve(
    path_expr: str,
    config: ResolverConfig | None = None,
) -> ResolverResult:
    """
    Expand path_expr and physically profile all discovered files.

    Returns a ResolverResult with a PhysicalFile per discovered file and
    any file-set-level findings.
    """
    if config is None:
        config = ResolverConfig()

    t0 = time.monotonic()
    result = ResolverResult()

    # --- Step 1: Glob expansion via DuckDB ---
    conn = duckdb.connect(database=":memory:", read_only=False)
    conn.execute("SET enable_progress_bar = false")

    try:
        paths = expand_glob(path_expr, conn)
    except Exception as e:
        result.set_findings.append(
            Finding(
                reason_code=ReasonCode.FORMAT_UNRECOGNIZED,
                severity=Severity.ERROR,
                locus="path_expression",
                expected=None,
                found=path_expr,
                explanation=f"Failed to expand path expression: {e}",
            )
        )
        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    # Zero-match is a finding, not an error
    if not paths:
        result.set_findings.append(
            Finding(
                reason_code=ReasonCode.FORMAT_UNRECOGNIZED,
                severity=Severity.WARN,
                locus="path_expression",
                expected="at least one file",
                found="0 files",
                explanation=f"Path expression matched no files: {path_expr!r}",
            )
        )
        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    # --- Step 2: File count ceiling ---
    if len(paths) > config.file_count_ceiling:
        result.set_findings.append(
            Finding(
                reason_code=ReasonCode.FILE_COUNT_CEILING,
                severity=Severity.WARN,
                locus="fileset",
                expected=f"<= {config.file_count_ceiling} files",
                found=f"{len(paths)} files",
                explanation=(
                    f"Scanned {config.file_count_ceiling} of {len(paths)} files "
                    f"(ceiling reached; raise with --file-ceiling)"
                ),
            )
        )
        paths = paths[: config.file_count_ceiling]

    # --- Step 3: Profile each file in parallel ---
    conn.close()  # Close the expansion connection

    num_threads = min(os.cpu_count() or 4, 8)
    if len(paths) < 10:
        num_threads = 1

    # Split paths into chunks for each thread
    chunks = [[] for _ in range(num_threads)]
    for i, path in enumerate(paths):
        chunks[i % num_threads].append(path)

    physical_files = []

    def _worker(paths_chunk):
        thread_conn = duckdb.connect(database=":memory:", read_only=False)
        thread_conn.execute("SET enable_progress_bar = false")
        results_chunk = []
        try:
            for p in paths_chunk:
                pf = _profile_one(p, thread_conn, config)
                results_chunk.append(pf)
        finally:
            thread_conn.close()
        return results_chunk

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_worker, chunk) for chunk in chunks if chunk]
        for fut in concurrent.futures.as_completed(futures):
            physical_files.extend(fut.result())

    # Restore the original order of paths
    path_to_idx = {p: idx for idx, p in enumerate(paths)}
    physical_files.sort(key=lambda pf: path_to_idx.get(pf.path, 0))
    result.physical_files = physical_files

    # Apply byte ceiling check sequentially over the ordered results
    total_bytes = 0
    byte_ceiling_hit = False
    for pf in result.physical_files:
        if pf.size_bytes:
            total_bytes += pf.size_bytes
        if not byte_ceiling_hit and total_bytes > config.byte_ceiling:
            byte_ceiling_hit = True
            result.set_findings.append(
                Finding(
                    reason_code=ReasonCode.BYTE_CEILING,
                    severity=Severity.WARN,
                    locus="fileset",
                    expected=f"<= {config.byte_ceiling:,} bytes",
                    found=f"{total_bytes:,} bytes",
                    explanation=(
                        f"Total scanned bytes exceeded ceiling ({config.byte_ceiling:,}). "
                        "Remaining files profiled at physical level only."
                    ),
                )
            )

    result.elapsed_ms = (time.monotonic() - t0) * 1000
    return result


# ---------------------------------------------------------------------------
# Single-file profiling pipeline
# ---------------------------------------------------------------------------

def _profile_one(
    path: Path,
    conn: duckdb.DuckDBPyConnection,
    config: ResolverConfig,
) -> PhysicalFile:
    """
    Run the full Tier 0 pipeline on a single file.
    Never raises — all errors are captured as bucket + error string.
    """
    size = get_file_size(path)
    pf = PhysicalFile(path=path, size_bytes=size)

    # Permission check
    if not os.access(path, os.R_OK):
        pf.bucket = Bucket.REFUSED
        pf.error = "Permission denied"
        return pf

    # Read up to 64KB sample for format and encoding detection once to save I/O
    try:
        with open(path, "rb") as fh:
            sample = fh.read(65536)
    except OSError as e:
        pf.bucket = Bucket.REFUSED
        pf.error = f"File read failed: {e}"
        return pf

    # Encoding detection (deterministic — no chardet)
    try:
        encoding, is_fallback = detect_encoding(sample)
        pf.encoding = encoding
        pf.encoding_is_fallback = is_fallback
    except Exception as e:
        pf.bucket = Bucket.DEGRADED
        pf.error = f"Encoding detection failed: {e}"
        return pf

    # Format detection (magic bytes first)
    try:
        fmt, confidence = detect_format(path, sample, encoding)
        pf.format_detected = fmt
        pf.format_confidence = confidence
    except Exception as e:
        pf.bucket = Bucket.DEGRADED
        pf.error = f"Format detection failed: {e}"
        return pf

    # Dispatch to appropriate profiler
    _dispatch_profiler(path, fmt, encoding, conn, pf, config)

    # In-worker canonicalization & Type inference for maximum query parallelization
    if pf.bucket == Bucket.PROFILED and pf.raw_column_names:
        from willitload.tier1.canonicalize import canonicalize_names, CanonicalizationConfig
        from willitload.tier2.sampler import sample_csv_types, sample_parquet_types

        norm_config = CanonicalizationConfig()
        normalized, traces = canonicalize_names(pf.raw_column_names, norm_config)
        pf.normalized_column_names = normalized

        try:
            if fmt in ("csv", "tsv"):
                sampled = sample_csv_types(
                    path=path,
                    normalized_names=normalized,
                    raw_names=pf.raw_column_names,
                    encoding=encoding,
                    delimiter=pf.delimiter or ",",
                    has_header=bool(pf.has_header),
                    conn=conn,
                )
                if sampled:
                    pf.column_types = {c.column_name: c.inferred_class for c in sampled.columns}
            elif fmt == "parquet":
                sampled = sample_parquet_types(path=path, normalized_names=pf.raw_column_names, conn=conn)
                if sampled:
                    pf.column_types = {c.column_name: c.inferred_class for c in sampled.columns}
        except Exception:
            pass

    return pf


def _dispatch_profiler(
    path: Path,
    fmt: str,
    encoding: str,
    conn: duckdb.DuckDBPyConnection,
    pf: PhysicalFile,
    config: ResolverConfig,
) -> None:
    """Route to the correct profiler based on detected format."""
    match fmt:
        case "csv" | "tsv" | "parquet" | "json" | "jsonl":
            profile_file(path, fmt, encoding, conn, pf)

        case "sqlite":
            parsers.profile_sqlite(path, pf)

        case "xml":
            parsers.profile_xml(path, pf)

        case "zip":
            # Archive: enumerate members, don't profile them here
            # (member profiling is a separate concern for future nested-archive support)
            members = parsers.profile_zip(path, pf, current_depth=pf.archive_depth)
            # For v1: log that archive was seen but members aren't recursively profiled
            # unless current_depth is 0 (top-level ZIP) — deferred for nested expansion
            _ = members  # future: recurse into members

        case "excel":
            parsers.profile_excel(path, pf)

        case "gzip":
            # gzip: DuckDB can read gzipped CSVs — attempt transparent read
            profile_file(path, "csv", encoding, conn, pf)
            if pf.bucket == Bucket.CATALOGUED:
                pf.error = "gzip content profiling failed; file catalogued"

        case _:
            pf.bucket = Bucket.CATALOGUED
            # Unknown format — physical fingerprint captured, not structurally profiled
