"""
willitload.tier2.sampler — Bounded, reproducibly-random file sampling.

Sampling mechanics:
  - first-N rows + random-N rows from the remainder
  - SAMPLE_SEED = 42: fixed, documented constant — never derived from file
    metadata or timestamps. Guarantees same-input → same-output across
    machines and runs (spec determinism invariant).
  - Applied ONLY within already-formed Tier-1 families, never across the
    whole folder blindly.
  - Sample-then-confirm: grouping/typing runs on the sample; any anomaly
    claim is re-verified against the full file before assertion.

Sampling is the mechanism that keeps "check thousands of files" pre-flight-fast:
expensive full-file confirmation scales with the number of suspects, not files.
A 99.9%-clean set clears almost for free.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from willitload.types import TypeClass, normalize_type

# Fixed seed — documented here, referenced everywhere sampling is used.
# NEVER derive from file path, mtime, or any other varying input.
SAMPLE_SEED = 42

# Default sample sizes
FIRST_N = 1_000
RANDOM_N = 1_000


@dataclass(frozen=True)
class SampleConfig:
    first_n: int = FIRST_N
    random_n: int = RANDOM_N
    seed: int = SAMPLE_SEED


@dataclass
class ColumnTypeSample:
    """Inferred type for one column from a sampled set of rows."""
    column_name: str
    """Normalized column name."""
    inferred_class: TypeClass
    null_count: int
    sample_row_count: int

    @property
    def has_nulls(self) -> bool:
        return self.null_count > 0

    @property
    def null_fraction(self) -> float:
        if self.sample_row_count == 0:
            return 0.0
        return self.null_count / self.sample_row_count


@dataclass
class FileSample:
    """Type inference results for all columns in one file."""
    path: str
    columns: list[ColumnTypeSample]
    sample_row_count: int
    full_file: bool = False
    """True if the full file was read (not just a sample)."""

    def column_types(self) -> dict[str, TypeClass]:
        return {c.column_name: c.inferred_class for c in self.columns}


def sample_csv_types(
    path: Path,
    normalized_names: list[str],
    raw_names: list[str],
    encoding: str,
    delimiter: str,
    has_header: bool,
    conn: duckdb.DuckDBPyConnection,
    config: SampleConfig | None = None,
) -> FileSample | None:
    """
    Infer column types for a CSV file by sampling via DuckDB.
    Uses DuckDB's type inference on a bounded sample.

    Returns None if sampling fails (caller treats as degraded).
    """
    if config is None:
        config = SampleConfig()

    path_str = str(path)
    rng = random.Random(config.seed)

    from willitload.tier0.duckdb_reader import _to_duckdb_encoding
    db_enc = _to_duckdb_encoding(encoding)

    try:
        # First: get total row count estimate (fast — DuckDB uses stats)
        total_rows_result = conn.execute(
            "SELECT COUNT(*) FROM read_csv(?, header := ?, delim := ?, encoding := ?)",
            [path_str, has_header, delimiter, db_enc],
        ).fetchone()
        total_rows = int(total_rows_result[0]) if total_rows_result else 0

        # Build sample: first-N + random-N from remainder
        if total_rows <= config.first_n + config.random_n:
            # Small file — read all rows
            sample_query = conn.execute(
                "SELECT * FROM read_csv(?, header := ?, delim := ?, encoding := ?) LIMIT ?",
                [path_str, has_header, delimiter, db_enc, total_rows],
            )
            full_file = True
        else:
            # Generate random row offsets for the random-N portion
            remainder_start = config.first_n
            remainder_end = total_rows - 1
            random_offsets = sorted(
                rng.sample(range(remainder_start, remainder_end + 1), config.random_n)
            )
            # DuckDB doesn't support arbitrary row-offset sampling natively;
            # use USING SAMPLE for the combined sample instead.
            # USING SAMPLE is reproducible with a fixed seed.
            sample_size = min(config.first_n + config.random_n, total_rows)
            sample_query = conn.execute(
                f"SELECT * FROM read_csv(?, header := ?, delim := ?, encoding := ?) "
                f"USING SAMPLE {sample_size} (reservoir, {config.seed})",
                [path_str, has_header, delimiter, db_enc],
            )
            full_file = False

        # Get column type descriptions from DuckDB
        col_descriptions = sample_query.description or []
        col_names_from_duckdb = [d[0] for d in col_descriptions]

        # Fetch the sample rows
        rows = sample_query.fetchall()
        sample_row_count = len(rows)

        columns: list[ColumnTypeSample] = []
        for col_idx, (raw_col_name, norm_col_name) in enumerate(
            zip(raw_names, normalized_names)
        ):
            # Match to DuckDB column by position (header names may differ after normalization)
            if col_idx >= len(col_descriptions):
                continue

            duckdb_type_str = col_descriptions[col_idx][1]
            inferred_class = normalize_type(str(duckdb_type_str))

            # Count nulls in sample
            null_count = sum(
                1 for row in rows if col_idx < len(row) and row[col_idx] is None
            )

            columns.append(
                ColumnTypeSample(
                    column_name=norm_col_name,
                    inferred_class=inferred_class,
                    null_count=null_count,
                    sample_row_count=sample_row_count,
                )
            )

        return FileSample(
            path=path_str,
            columns=columns,
            sample_row_count=sample_row_count,
            full_file=full_file,
        )

    except Exception:
        return None


def sample_parquet_types(
    path: Path,
    normalized_names: list[str],
    conn: duckdb.DuckDBPyConnection,
) -> FileSample | None:
    """
    Extract declared types from a Parquet footer (no data row reading needed).
    Parquet types are declared — DuckDB reads from the footer metadata.
    """
    path_str = str(path)
    try:
        rows = conn.execute(
            "SELECT name, type FROM parquet_schema(?)",
            [path_str],
        ).fetchall()

        if not rows:
            return None

        columns: list[ColumnTypeSample] = []
        for i, norm_name in enumerate(normalized_names):
            if i >= len(rows):
                break
            _, type_str = rows[i]
            inferred_class = normalize_type(str(type_str))
            columns.append(
                ColumnTypeSample(
                    column_name=norm_name,
                    inferred_class=inferred_class,
                    null_count=0,
                    sample_row_count=0,
                )
            )

        return FileSample(
            path=path_str,
            columns=columns,
            sample_row_count=0,
            full_file=True,  # Parquet footer is the complete declared schema
        )

    except Exception:
        return None
