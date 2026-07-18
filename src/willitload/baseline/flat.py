"""
willitload.baseline.flat — Flat schema file parser (primary baseline front-door).

Format: one column per line, `name<sep>type`
  - Separator: comma, tab, space, or colon (auto-detected from first data line)
  - Type is normalized through the alias map
  - Lines beginning with # are comments; blank lines are ignored
  - Order is preserved (required for position-mode checks)

Example:
    customer_id,int
    order_date,date
    amount,decimal
    notes,text
"""

from __future__ import annotations

import re
from pathlib import Path

from willitload.baseline.fingerprint import BaselineFingerprint, BaselineColumn
from willitload.tier1.canonicalize import canonicalize_name
from willitload.types import normalize_type, TypeClass

_SEPARATORS = [",", "\t", ":", " "]


def _detect_separator(first_data_line: str) -> str:
    """Detect the separator from the first data line."""
    for sep in _SEPARATORS:
        if sep in first_data_line:
            return sep
    return ","  # default


def parse_flat_schema(path: str | Path) -> BaselineFingerprint:
    """
    Parse a flat schema file into a BaselineFingerprint.

    Raises ValueError on malformed input.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ValueError(f"Cannot read baseline file {path}: {e}") from e

    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    if not lines:
        raise ValueError(f"Baseline file {path} contains no data lines")

    sep = _detect_separator(lines[0])

    columns: list[BaselineColumn] = []
    for i, line in enumerate(lines):
        parts = line.strip().split(sep, maxsplit=1)
        if len(parts) == 1:
            # Name-only line → type defaults to ANY (unconstrained)
            raw_name = parts[0].strip()
            raw_type = "any"
        elif len(parts) == 2:
            raw_name, raw_type = parts[0].strip(), parts[1].strip()
        else:
            raise ValueError(f"Baseline line {i+1} has unexpected format: {line!r}")

        trace = canonicalize_name(raw_name)
        type_class = normalize_type(raw_type)

        columns.append(
            BaselineColumn(
                name=trace.normalized,
                raw_name=raw_name,
                type_class=type_class,
                position=i,
            )
        )

    return BaselineFingerprint(
        source_description=f"flat schema file: {path}",
        columns=columns,
        source_path=path,
    )
