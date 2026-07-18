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
    from willitload.tier0.resolver import _profile_one, ResolverConfig
    from willitload.models import Bucket
    from willitload.types import TypeClass

    conn = duckdb.connect(":memory:")
    conn.execute("SET enable_progress_bar = false")

    try:
        pf = _profile_one(path, conn, ResolverConfig())
    finally:
        conn.close()

    if pf.bucket == Bucket.REFUSED:
        raise ValueError(f"Golden file {path} could not be read: {pf.error}")

    if not pf.raw_column_names:
        raise ValueError(
            f"Golden file {path} could not be profiled — "
            f"no column names extracted (format: {pf.format_detected!r})"
        )

    columns: list[BaselineColumn] = []
    for i, raw_name in enumerate(pf.raw_column_names):
        trace = canonicalize_name(raw_name)
        type_class = pf.column_types.get(raw_name, TypeClass.ANY)
        columns.append(
            BaselineColumn(
                name=trace.normalized,
                raw_name=raw_name,
                type_class=type_class,
                position=i,
            )
        )

    return BaselineFingerprint(
        source_description=f"golden sample file: {path} (format: {pf.format_detected})",
        columns=columns,
        source_path=path,
    )
