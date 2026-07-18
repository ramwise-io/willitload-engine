"""
willitload.tier0.duckdb_reader — DuckDB-backed file profiling.

DuckDB is the engine for CSV/JSON/Parquet I/O, glob expansion, and type inference.
Python is the orchestrator only — no file scanning in Python loops.

Discipline (§3.1 — non-negotiable):
  DuckDB does the file I/O and heavy compute.
  Python ONLY orchestrates: expand glob → call DuckDB → assemble PhysicalFile structs.
  Never write Python loops that read file contents row-by-row.

Path/baseline expressions are ALWAYS passed as DuckDB parameters, never
interpolated into SQL strings. No quoting footguns, no injection-shaped bugs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb

from willitload.tier0.physical import PhysicalFile
from willitload.models import Bucket
from willitload.types import normalize_type


# ---------------------------------------------------------------------------
# DuckDB connection factory
# ---------------------------------------------------------------------------

def _get_conn() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection for read-only profiling."""
    conn = duckdb.connect(database=":memory:", read_only=False)
    # Disable progress bars and info messages in output
    conn.execute("SET enable_progress_bar = false")
    return conn


# ---------------------------------------------------------------------------
# Glob expansion
# ---------------------------------------------------------------------------

def expand_glob(path_expr: str, conn: duckdb.DuckDBPyConnection | None = None) -> list[Path]:
    """
    Expand a path expression (directory or glob) to a list of absolute file paths.
    Uses DuckDB's glob() table function.

    Zero-match → returns empty list (caller emits a finding, not an exception).
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    try:
        path_str = str(path_expr)

        # If it's a plain directory with no glob characters, append /**/*
        if not any(c in path_str for c in ("*", "?", "[")) and Path(path_str).is_dir():
            glob_expr = path_str.rstrip("/\\") + "/**/*"
        else:
            glob_expr = path_str

        # Use DuckDB glob() — paths passed as parameter, never interpolated
        rows = conn.execute("SELECT * FROM glob(?)", [glob_expr]).fetchall()
        paths = []
        path_expr_parts = Path(path_expr).parts
        for (p,) in rows:
            candidate = Path(p)
            if candidate.is_file():
                parts = candidate.parts
                has_dot_part = False
                for part in parts:
                    if part.startswith(".") and part not in path_expr_parts:
                        has_dot_part = True
                        break
                if not has_dot_part:
                    paths.append(candidate.resolve())
        return paths

    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CSV profiling via DuckDB
# ---------------------------------------------------------------------------

def _duckdb_csv_columns(
    path: Path,
    conn: duckdb.DuckDBPyConnection,
    encoding: str = "utf-8",
) -> tuple[list[str], list[str], str | None, str | None, bool]:
    """
    Use DuckDB's CSV sniffer to extract:
        (raw_column_names, inferred_types, delimiter, quote_char, has_header)

    Returns empty lists on failure.
    Note: DuckDB 1.x sniff_csv does not accept an encoding parameter;
    encoding is handled at the OS/read level by the resolver before we get here.
    """
    path_str = str(path)

    try:
        # Use sniff_csv to get structural info — no encoding param in DuckDB 1.x
        sniffer = conn.execute(
            "SELECT * FROM sniff_csv(?)",
            [path_str],
        ).fetchone()

        if sniffer is None:
            return [], [], None, None, True

        # Build a dict from column names + values for version-robust access
        row_dict = dict(
            zip(
                [d[0] for d in conn.description],
                sniffer,
            )
        )

        delimiter = row_dict.get("Delimiter") or ","
        quote_char = row_dict.get("Quote") or '"'
        has_header_val = row_dict.get("HasHeader", True)

        # Columns is a list of dicts: [{"name": "col1", "type": "BIGINT"}, ...]
        columns_raw = row_dict.get("Columns") or []
        raw_names: list[str] = []
        inferred_types: list[str] = []

        if isinstance(columns_raw, list):
            for col in columns_raw:
                if isinstance(col, dict):
                    raw_names.append(str(col.get("name", "")))
                    inferred_types.append(str(col.get("type", "text")))
                elif isinstance(col, (list, tuple)) and len(col) >= 2:
                    raw_names.append(str(col[0]))
                    inferred_types.append(str(col[1]))
        elif isinstance(columns_raw, str):
            import json
            try:
                parsed = json.loads(columns_raw)
                for col in parsed:
                    raw_names.append(str(col.get("name", "")))
                    inferred_types.append(str(col.get("type", "text")))
            except Exception:
                pass

        if len(raw_names) == 1 and any(c in raw_names[0] for c in (",", "\t", ";")):
            raise ValueError("Sniffer fell back to single column containing delimiters")

        return (
            raw_names,
            inferred_types,
            str(delimiter),
            str(quote_char),
            bool(has_header_val),
        )

    except Exception:
        # Fallback for ragged/problematic files that fail sniff_csv.
        # We read the first line in Python to extract delimiter and column names.
        try:
            with open(path, "r", encoding=encoding, errors="replace") as fh:
                first_line = fh.readline().strip()
            if not first_line:
                return [], [], None, None, True

            # Count candidates in the first line
            candidates = [",", "\t", ";", "|"]
            counts = {c: first_line.count(c) for c in candidates}
            best_delim = max(counts, key=counts.get)
            if counts[best_delim] == 0:
                best_delim = ","

            # Simple split to extract names
            raw_names = [col.strip('"\' ') for col in first_line.split(best_delim)]
            inferred_types = ["VARCHAR"] * len(raw_names)
            return raw_names, inferred_types, best_delim, '"', True
        except Exception:
            return [], [], None, None, True


def _to_duckdb_encoding(enc: str) -> str:
    """Map our encoding names to DuckDB-accepted encoding strings."""
    _MAP = {
        "utf-8":       "UTF8",
        "utf-8-sig":   "UTF8",
        "utf-16-le":   "UTF16",
        "utf-16-be":   "UTF16",
        "utf-32-le":   "UTF32",
        "utf-32-be":   "UTF32",
        "latin-1":     "LATIN1",
    }
    return _MAP.get(enc.lower(), "UTF8")


# ---------------------------------------------------------------------------
# Parquet profiling via DuckDB
# ---------------------------------------------------------------------------

def _duckdb_parquet_columns(
    path: Path,
    conn: duckdb.DuckDBPyConnection,
) -> tuple[list[str], list[str], int | None]:
    """
    Extract column names, types, and row count from a Parquet footer.
    Returns (names, types, row_count). Types are DuckDB type strings.
    """
    path_str = str(path)
    try:
        # parquet_schema() reads only the footer — fast
        rows = conn.execute(
            "SELECT name, type FROM parquet_schema(?)",
            [path_str],
        ).fetchall()

        # Filter out group-level schema rows (they have no type or type = 'REQUIRED')
        names = []
        types = []
        for name, typ in rows:
            if name and typ and typ.upper() not in ("REQUIRED", "OPTIONAL", "REPEATED"):
                names.append(str(name))
                types.append(str(typ))

        # Row count from metadata (fast — footer only)
        try:
            count_row = conn.execute(
                "SELECT num_rows FROM parquet_metadata(?)",
                [path_str],
            ).fetchone()
            row_count = int(count_row[0]) if count_row else None
        except Exception:
            row_count = None

        return names, types, row_count

    except Exception:
        return [], [], None


# ---------------------------------------------------------------------------
# JSON / JSONL profiling via DuckDB
# ---------------------------------------------------------------------------

def _duckdb_json_columns(
    path: Path,
    fmt: str,
    conn: duckdb.DuckDBPyConnection,
) -> tuple[list[str], list[str]]:
    """
    Infer JSON/JSONL leaf-path set and types via DuckDB.
    Returns (field_names, types).
    """
    path_str = str(path)
    try:
        if fmt == "jsonl":
            # JSONL: read as line-delimited JSON
            rows = conn.execute(
                "DESCRIBE SELECT * FROM read_json(?, format := 'newline_delimited', maximum_object_size := 16777216)",
                [path_str],
            ).fetchall()
        else:
            rows = conn.execute(
                "DESCRIBE SELECT * FROM read_json(?, maximum_object_size := 16777216)",
                [path_str],
            ).fetchall()

        names = [r[0] for r in rows]
        types = [r[1] for r in rows]
        return names, types

    except Exception:
        return [], []


# ---------------------------------------------------------------------------
# File size helper
# ---------------------------------------------------------------------------

def get_file_size(path: Path) -> int:
    """Return file size in bytes; 0 on error."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Profile dispatch — main entry point called by the resolver
# ---------------------------------------------------------------------------

def profile_file(
    path: Path,
    fmt: str,
    encoding: str,
    conn: duckdb.DuckDBPyConnection,
    pf: PhysicalFile,
) -> None:
    """
    Populate `pf` (a PhysicalFile) with structural columns and types
    using DuckDB, based on the detected format.

    Mutates `pf` in place. On failure, degrades gracefully.
    """
    match fmt:
        case "csv" | "tsv":
            names, types, delim, quote, has_header = _duckdb_csv_columns(path, conn, encoding)
            pf.raw_column_names = names
            pf.column_count = len(names)
            pf.delimiter = delim
            pf.quote_char = quote
            pf.has_header = has_header
            if names:
                pf.bucket = Bucket.PROFILED

        case "parquet":
            names, types, row_count = _duckdb_parquet_columns(path, conn)
            pf.raw_column_names = names
            pf.column_count = len(names)
            pf.row_count_estimate = row_count
            pf.has_header = True  # Parquet always has a schema — equivalent to named columns
            if names:
                pf.bucket = Bucket.PROFILED

        case "json" | "jsonl":
            names, types = _duckdb_json_columns(path, fmt, conn)
            pf.raw_column_names = names
            pf.column_count = len(names)
            pf.has_header = True  # JSON keys are always "named"
            if names:
                pf.bucket = Bucket.PROFILED

        case _:
            # Format not handled by DuckDB reader; leave for specialized parsers
            pass
