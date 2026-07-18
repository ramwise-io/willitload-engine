"""
willitload.types — Type vocabulary, alias normalization, and compatibility lattice.

Coarse, closed class set (§6). NOT precise SQL types — only what the engine can
verify from samples and what structural equivalence actually requires.

Rules:
- Alias map is applied symmetrically on baseline ingestion AND type inference output.
- Compatibility lattice is structural only — no loader semantics.
- The alias map is also the on-ramp to future DDL ingestion (DDL types = more aliases).
"""

from __future__ import annotations

from enum import Enum


class TypeClass(str, Enum):
    """
    The closed class set used throughout the engine.
    Values are the canonical string forms emitted in JSON output.
    """
    INT = "int"
    DECIMAL = "decimal"
    BOOL = "bool"
    DATE = "date"
    TIMESTAMP = "timestamp"
    TEXT = "text"
    BLOB = "blob"
    ANY = "any"
    """Present-but-type-unconstrained. Used when no tighter class can be determined."""


class Compatibility(str, Enum):
    """Structural compatibility between two TypeClass values."""
    IDENTICAL = "identical"
    WIDENING = "widening"
    """e.g. int → decimal, narrower text → wider text. Non-breaking structurally."""
    BREAKING = "breaking"
    """e.g. decimal → text, date → int. The load will miscast or fail."""


# ---------------------------------------------------------------------------
# Alias normalization map
#
# Applied symmetrically on:
#   1. Baseline ingestion (normalize declared types to TypeClass)
#   2. Type inference output (normalize inferred DuckDB types to TypeClass)
#
# Keys are lowercase aliases. Values are TypeClass members.
# This map is also the on-ramp for future DDL type ingestion — DDL type names
# are just more aliases that map to the same coarse classes.
# ---------------------------------------------------------------------------

ALIAS_MAP: dict[str, TypeClass] = {
    # INT
    "int":       TypeClass.INT,
    "integer":   TypeClass.INT,
    "int8":      TypeClass.INT,
    "int16":     TypeClass.INT,
    "int32":     TypeClass.INT,
    "int64":     TypeClass.INT,
    "bigint":    TypeClass.INT,
    "smallint":  TypeClass.INT,
    "tinyint":   TypeClass.INT,
    "long":      TypeClass.INT,
    "hugeint":   TypeClass.INT,
    "ubigint":   TypeClass.INT,
    "uinteger":  TypeClass.INT,
    "usmallint": TypeClass.INT,
    "utinyint":  TypeClass.INT,

    # DECIMAL
    "decimal":   TypeClass.DECIMAL,
    "float":     TypeClass.DECIMAL,
    "float4":    TypeClass.DECIMAL,
    "float8":    TypeClass.DECIMAL,
    "double":    TypeClass.DECIMAL,
    "numeric":   TypeClass.DECIMAL,
    "number":    TypeClass.DECIMAL,
    "real":      TypeClass.DECIMAL,
    "money":     TypeClass.DECIMAL,

    # BOOL
    "bool":      TypeClass.BOOL,
    "boolean":   TypeClass.BOOL,
    "bit":       TypeClass.BOOL,

    # DATE
    "date":      TypeClass.DATE,

    # TIMESTAMP
    "timestamp":          TypeClass.TIMESTAMP,
    "datetime":           TypeClass.TIMESTAMP,
    "timestamp with time zone": TypeClass.TIMESTAMP,
    "timestamptz":        TypeClass.TIMESTAMP,
    "timestamp_s":        TypeClass.TIMESTAMP,
    "timestamp_ms":       TypeClass.TIMESTAMP,
    "timestamp_us":       TypeClass.TIMESTAMP,
    "timestamp_ns":       TypeClass.TIMESTAMP,

    # TEXT
    "text":      TypeClass.TEXT,
    "string":    TypeClass.TEXT,
    "varchar":   TypeClass.TEXT,
    "char":      TypeClass.TEXT,
    "nvarchar":  TypeClass.TEXT,
    "nchar":     TypeClass.TEXT,
    "str":       TypeClass.TEXT,
    "utf8":      TypeClass.TEXT,
    "enum":      TypeClass.TEXT,

    # BLOB
    "blob":      TypeClass.BLOB,
    "binary":    TypeClass.BLOB,
    "varbinary": TypeClass.BLOB,
    "bytea":     TypeClass.BLOB,

    # ANY / unconstrained
    "any":       TypeClass.ANY,
    "*":         TypeClass.ANY,
    "unknown":   TypeClass.ANY,
    "interval":  TypeClass.ANY,  # no coarse class maps cleanly; surface as any
    "json":      TypeClass.ANY,  # DuckDB JSON type; surface as any for now
    "map":       TypeClass.ANY,
    "list":      TypeClass.ANY,
    "struct":    TypeClass.ANY,
    "union":     TypeClass.ANY,
}


def normalize_type(raw: str) -> TypeClass:
    """
    Normalize a raw type string (from a baseline file or DuckDB inference)
    to a TypeClass using the alias map.

    Normalization: lowercase + strip whitespace before lookup.
    Unknown types → TypeClass.ANY (never raise; always return something).
    """
    key = raw.strip().lower()
    # Handle DuckDB parameterized types: e.g. "decimal(10,2)" → "decimal"
    if "(" in key:
        key = key.split("(")[0].strip()
    return ALIAS_MAP.get(key, TypeClass.ANY)


# ---------------------------------------------------------------------------
# Compatibility lattice
#
# Structural compatibility only — no loader semantics modeled.
# The lattice answers: "if the baseline declares A and we observe B, is that OK?"
# ---------------------------------------------------------------------------

# Widening pairs (baseline_type, observed_type) — order matters
# Answers: "If baseline expects A, and we observe B, is that OK (safe coercion)?"
_WIDENING_PAIRS: frozenset[tuple[TypeClass, TypeClass]] = frozenset({
    # Integer can be safely loaded into a decimal column
    (TypeClass.DECIMAL, TypeClass.INT),
    # Integer, decimal, date, timestamp, bool, blob can all be safely loaded into a text column
    (TypeClass.TEXT, TypeClass.INT),
    (TypeClass.TEXT, TypeClass.DECIMAL),
    (TypeClass.TEXT, TypeClass.DATE),
    (TypeClass.TEXT, TypeClass.TIMESTAMP),
    (TypeClass.TEXT, TypeClass.BOOL),
    (TypeClass.TEXT, TypeClass.BLOB),
    # Date can be safely loaded into a timestamp column
    (TypeClass.TIMESTAMP, TypeClass.DATE),
    # Boolean can be safely loaded into an integer column (often as 0/1)
    (TypeClass.INT, TypeClass.BOOL),
})

# The ANY wildcard is compatible with everything
_ANY_COMPATIBLE: frozenset[TypeClass] = frozenset(TypeClass)


def check_compatibility(baseline: TypeClass, observed: TypeClass) -> Compatibility:
    """
    Return the structural compatibility of `observed` against `baseline`.

    baseline: what the baseline declares
    observed: what the engine inferred from the file
    """
    if baseline == TypeClass.ANY or observed == TypeClass.ANY:
        return Compatibility.IDENTICAL  # ANY is unconstrained; no conflict
    if baseline == observed:
        return Compatibility.IDENTICAL
    if (baseline, observed) in _WIDENING_PAIRS:
        return Compatibility.WIDENING
    return Compatibility.BREAKING
