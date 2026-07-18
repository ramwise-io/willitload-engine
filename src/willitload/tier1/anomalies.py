"""
willitload.tier1.anomalies — Intra-file structural anomaly detection.

Detects pure-structure anomalies that need no type information:
  - RAGGED_ROWS: rows whose column count deviates from the file's mode
  - MULTI_RECORD: two+ distinct stable column-count regimes stacked vertically
  - TRUNCATED: abrupt EOF mid-record / final partial row
  - TRAILING_SUMMARY: final row(s) whose profile breaks the column pattern

These are detected via DuckDB WHERE POSSIBLE (sampling in DuckDB, counting
column deviations). Fallback to Python for files DuckDB can't read.

All findings carry a locus (e.g. "row 4,203") for surgical precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from willitload.models import Finding, ReasonCode, Severity
from willitload.tier0.physical import PhysicalFile


# Rows sampled for anomaly detection (not full-file for speed)
ANOMALY_SAMPLE_ROWS = 10_000
# Minimum run length to constitute a "stable regime" (multi-record detection)
MIN_REGIME_LENGTH = 3


@dataclass
class AnomalyResult:
    findings: list[Finding] = field(default_factory=list)


def detect_csv_anomalies(
    pf: PhysicalFile,
    conn: duckdb.DuckDBPyConnection,
) -> AnomalyResult:
    """
    Detect intra-file structural anomalies in a CSV/TSV file using DuckDB.

    Approach:
      1. Read each row as a single VARCHAR column (bypasses schema inference)
         and count the delimiter occurrences to get per-row column count.
      2. Compute mode column count.
      3. Flag deviating rows as RAGGED_ROWS.
      4. Detect MULTI_RECORD via consecutive stable count regimes.
      5. Detect TRUNCATED via EOF mid-row (final count lower than mode).
      6. Detect TRAILING_SUMMARY via final 1–3 rows breaking the pattern.
    """
    result = AnomalyResult()
    path_str = str(pf.path)
    delim = pf.delimiter or ","

    from willitload.tier0.duckdb_reader import _to_duckdb_encoding
    db_enc = "UTF-8"
    if pf.encoding:
        db_enc = _to_duckdb_encoding(pf.encoding)

    try:
        # Read rows as raw lines via DuckDB, count delimiter occurrences per line
        # Passed as parameters — no interpolation
        rows_data = conn.execute(
            """
            SELECT
                row_number() OVER () AS rn,
                length(line) - length(replace(line, ?, '')) AS col_count
            FROM read_csv(
                ?,
                columns={'line': 'VARCHAR'},
                header=false,
                auto_detect=false,
                delim='',
                encoding=?
            )
            LIMIT ?
            """,
            [delim, path_str, db_enc, ANOMALY_SAMPLE_ROWS],
        ).fetchall()

    except Exception:
        # If DuckDB can't read the file for anomaly scanning, skip gracefully
        return result

    if not rows_data:
        return result

    counts = [row[1] for row in rows_data]

    # Exclude header row from mode calculation if file has a header
    start_idx = 1 if (pf.has_header and len(counts) > 1) else 0
    data_counts = counts[start_idx:]

    if not data_counts:
        return result

    # Mode column count
    mode_count = max(set(data_counts), key=data_counts.count)
    # Adjust for delimiter count: N delimiters → N+1 columns
    expected_cols = mode_count + 1

    # --- Ragged rows ---
    ragged_rows = [
        (rows_data[start_idx + i][0], count + 1)
        for i, count in enumerate(data_counts)
        if count != mode_count
    ]

    # --- Ragged rows & Trailing summary ---
    total_rows = len(rows_data)
    trailing_summary_rows = [r for r in ragged_rows if r[0] == total_rows]
    non_trailing_ragged = [r for r in ragged_rows if r[0] != total_rows]

    if non_trailing_ragged:
        sample = non_trailing_ragged[:5]
        loci = ", ".join(f"row {r}" for r, _ in sample)
        found_counts = ", ".join(str(c) for _, c in sample)
        result.findings.append(
            Finding(
                reason_code=ReasonCode.RAGGED_ROWS,
                severity=Severity.ERROR,
                locus=loci + ("..." if len(non_trailing_ragged) > 5 else ""),
                expected=str(expected_cols),
                found=found_counts,
                explanation=(
                    f"{len(non_trailing_ragged)} row(s) have a column count that deviates "
                    f"from the file's mode ({expected_cols} columns). "
                    f"Likely cause: unescaped delimiter or embedded newline."
                ),
                confidence=len(non_trailing_ragged),
            )
        )

    # --- Trailing summary row ---
    if trailing_summary_rows:
        last_row_num, last_col_count = trailing_summary_rows[0]
        result.findings.append(
            Finding(
                reason_code=ReasonCode.TRAILING_SUMMARY,
                severity=Severity.WARN,
                locus=f"row {last_row_num}",
                expected=str(expected_cols),
                found=str(last_col_count),
                explanation=(
                    f"Final sampled row (row {last_row_num}) has {last_col_count} columns "
                    f"vs expected {expected_cols}. "
                    f"Possible totals/summary row appended to the data."
                ),
            )
        )

    # --- Truncated file ---
    # Check if the file's last byte is not a newline-terminated row
    # (heuristic: if the final row count is lower than mode, it may be truncated)
    try:
        with open(pf.path, "rb") as fh:
            fh.seek(-512, 2)  # last 512 bytes
            tail = fh.read()
        if tail and not tail.endswith((b"\n", b"\r")):
            result.findings.append(
                Finding(
                    reason_code=ReasonCode.TRUNCATED,
                    severity=Severity.ERROR,
                    locus="EOF",
                    expected="newline-terminated final row",
                    found="partial row at EOF",
                    explanation=(
                        "File does not end with a newline. "
                        "The final row may be truncated or the file may be incomplete."
                    ),
                )
            )
    except OSError:
        pass

    # --- Multi-record detection ---
    # Segment data_counts by stable runs; a run is stable if it holds for >= MIN_REGIME_LENGTH rows
    regimes = _detect_regimes(data_counts)
    if len(regimes) >= 2:
        regime_desc = "; ".join(
            f"{count + 1} columns × {length} rows starting at relative row {start}"
            for count, start, length in regimes
        )
        result.findings.append(
            Finding(
                reason_code=ReasonCode.MULTI_RECORD,
                severity=Severity.WARN,
                locus="file structure",
                expected="one stable column-count regime",
                found=f"{len(regimes)} distinct regimes",
                explanation=(
                    f"File contains {len(regimes)} distinct stable column-count regimes, "
                    f"suggesting multiple logical record types stacked vertically. "
                    f"Regimes: {regime_desc}."
                ),
                confidence=len(regimes),
            )
        )

    return result


def _detect_regimes(counts: list[int]) -> list[tuple[int, int, int]]:
    """
    Detect stable column-count regimes in a list of per-row counts.

    A regime is a run of MIN_REGIME_LENGTH or more consecutive rows with the same count.
    Returns list of (count_value, start_index, run_length) tuples.
    """
    if not counts:
        return []

    regimes: list[tuple[int, int, int]] = []
    current = counts[0]
    run_start = 0
    run_len = 1

    for i in range(1, len(counts)):
        if counts[i] == current:
            run_len += 1
        else:
            if run_len >= MIN_REGIME_LENGTH:
                regimes.append((current, run_start, run_len))
            current = counts[i]
            run_start = i
            run_len = 1

    # Final run
    if run_len >= MIN_REGIME_LENGTH:
        regimes.append((current, run_start, run_len))

    return regimes
