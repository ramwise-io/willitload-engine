"""
willitload.baseline.golden — Golden sample file baseline front-door.

Point at an actual file declared correct; fingerprint it with the scanner
and use that as the baseline. Free (reuses the scanner). This is also how
"match this Parquet/Avro schema" is supported without adding schema-format parsers.
"""

from __future__ import annotations

from pathlib import Path

from willitload.baseline.fingerprint import BaselineFingerprint, BaselineColumn
from willitload.tier0.encoding import detect_encoding
from willitload.tier0.format_detect import detect_format
from willitload.tier1.canonicalize import canonicalize_name
from willitload.types import normalize_type


def parse_golden_file(path: str | Path) -> BaselineFingerprint:
    """
    Fingerprint a declared-correct sample file and return it as a BaselineFingerprint.

    Uses the same Tier 0 detection pipeline as the main scan, so any format
    supported by the scanner is also supported as a golden-file baseline.

    Raises ValueError if the file cannot be profiled.
    """
    path = Path(path)
    if not path.exists():
        raise ValueError(f"Golden file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Golden file path is not a file: {path}")

    import duckdb
    from willitload.tier0.physical import PhysicalFile
    from willitload.models import Bucket
    from willitload.tier0.duckdb_reader import profile_file

    # Detect encoding and format
    try:
        with open(path, "rb") as fh:
            sample = fh.read(65536)
    except OSError as e:
        raise ValueError(f"Could not read golden file {path}: {e}")

    encoding, is_fallback = detect_encoding(sample)
    fmt, _confidence = detect_format(path, sample, encoding)

    conn = duckdb.connect(":memory:")
    conn.execute("SET enable_progress_bar = false")

    pf = PhysicalFile(path=path, size_bytes=path.stat().st_size, encoding=encoding, format_detected=fmt)

    try:
        profile_file(path, fmt, encoding, conn, pf)
    finally:
        conn.close()

    if not pf.raw_column_names:
        raise ValueError(
            f"Golden file {path} could not be profiled — "
            f"no column names extracted (format: {fmt!r})"
        )

    columns: list[BaselineColumn] = []
    for i, raw_name in enumerate(pf.raw_column_names):
        trace = canonicalize_name(raw_name)
        type_class = normalize_type("any")  # golden-file baseline: types from Tier 2 if needed
        columns.append(
            BaselineColumn(
                name=trace.normalized,
                raw_name=raw_name,
                type_class=type_class,
                position=i,
            )
        )

    return BaselineFingerprint(
        source_description=f"golden sample file: {path} (format: {fmt})",
        columns=columns,
    )
