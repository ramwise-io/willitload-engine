"""
willitload.baseline.from_json — Prior-scan JSON round-trip baseline parser.

Accepts the tool's own `--json` output as a baseline.
Git-committable as a CI fixture — the scan output of a known-good fileset
becomes the baseline for the next run.

Reads the `file_verdicts` array from a ScanResult JSON and synthesizes a
BaselineFingerprint from the most common structural family (the "golden" family).
If multiple families exist, uses the largest family and warns.
"""

from __future__ import annotations

import json
from pathlib import Path

from willitload.baseline.fingerprint import BaselineFingerprint, BaselineColumn
from willitload.tier1.canonicalize import canonicalize_name
from willitload.types import normalize_type


def parse_from_scan_json(path: str | Path) -> BaselineFingerprint:
    """
    Parse a prior scan's JSON output into a BaselineFingerprint.

    Raises ValueError on malformed or incompatible input.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"Cannot read scan JSON: {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e

    # Support both ScanResult and CheckResult JSON shapes
    if "file_verdicts" not in data and "golden" in data:
        # CheckResult: use golden files as the baseline source
        verdicts = data.get("golden", [])
    elif "file_verdicts" in data:
        verdicts = data["file_verdicts"]
    else:
        raise ValueError(
            f"JSON file {path} does not look like willitload scan or check output "
            f"(missing 'file_verdicts' or 'golden' key)"
        )

    # Find the most common column set across profiled files
    col_set_counts: dict[str, list[str]] = {}
    for v in verdicts:
        names = v.get("normalized_column_names") or v.get("raw_column_names") or []
        if not names:
            continue
        key = "|".join(names)
        col_set_counts.setdefault(key, names)

    if not col_set_counts:
        raise ValueError(f"No column information found in {path}")

    # Frequency-sort and pick the majority
    from collections import Counter
    freq: Counter[str] = Counter()
    for v in verdicts:
        names = v.get("normalized_column_names") or v.get("raw_column_names") or []
        if names:
            freq["|".join(names)] += 1

    best_key, _ = freq.most_common(1)[0]
    best_names = col_set_counts[best_key]

    # Build columns — types come from the first matching verdict's column_types
    col_types: dict[str, str] = {}
    for v in verdicts:
        names = v.get("normalized_column_names") or v.get("raw_column_names") or []
        if names and "|".join(names) == best_key:
            col_types = v.get("column_types", {})
            break

    columns: list[BaselineColumn] = []
    for i, name in enumerate(best_names):
        trace = canonicalize_name(name)
        raw_type = col_types.get(name, "any")
        type_class = normalize_type(str(raw_type))
        columns.append(
            BaselineColumn(
                name=trace.normalized,
                raw_name=name,
                type_class=type_class,
                position=i,
            )
        )

    return BaselineFingerprint(
        source_description=f"prior scan JSON: {path}",
        columns=columns,
    )
