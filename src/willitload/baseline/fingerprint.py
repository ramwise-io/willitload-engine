"""
willitload.baseline.fingerprint — The common internal baseline representation.

All three baseline front-doors normalize to BaselineFingerprint.
The check engine operates against this type only — it never knows which
front-door was used to produce it.

Baseline carries NO behavior. Alignment mode and extra-column policy are
CLI flags on `check`, NOT directives in the baseline. The same baseline
can be checked strictly today and loosely tomorrow without editing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from willitload.types import TypeClass


@dataclass(frozen=True)
class BaselineColumn:
    """One column in the baseline declaration."""
    name: str
    """Normalized (canonicalized) column name."""
    raw_name: str
    """Original name as declared in the baseline file."""
    type_class: TypeClass
    position: int
    """0-indexed position in the declared schema."""


@dataclass
class BaselineFingerprint:
    """
    The internal representation of a baseline.

    Produced by any of the three front-doors and consumed only by check.py.
    Position ordering is preserved — required for position-mode checks.
    """
    source_description: str
    """Human-readable description of where this fingerprint came from."""
    columns: list[BaselineColumn]

    @property
    def name_set(self) -> frozenset[str]:
        return frozenset(c.name for c in self.columns)

    @property
    def ordered_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def ordered_types(self) -> list[TypeClass]:
        return [c.type_class for c in self.columns]

    @property
    def name_to_type(self) -> dict[str, TypeClass]:
        return {c.name: c.type_class for c in self.columns}

    @property
    def column_count(self) -> int:
        return len(self.columns)
