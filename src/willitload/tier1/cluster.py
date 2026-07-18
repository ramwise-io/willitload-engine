"""
willitload.tier1.cluster — Structural family assignment and Jaccard similarity.

Groups files into families based on their normalized column-name sets.
Conservative bias: prefer "these are separate, here's the difference" over
confident over-merging. One confident wrong grouping destroys trust.

Structural ladder (name-only):
  exact → reordered → additive (superset) → subset (missing) → partial → disjoint

Jaccard similarity: |A ∩ B| / |A ∪ B|
Used for graded overlap reporting ("28 of 30 match"), not for grouping decisions.
Grouping is by exact normalized-set identity — deterministic, no threshold guessing.

Cluster assignment per file + confidence = count of independent agreeing signals.
  - Name-only agreement = confidence 1
  - Name + type agreement = confidence 2 (Tier 2 raises this)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StructuralRelation(str, Enum):
    """Structural relationship between two column-name sets (or a file vs its family)."""
    EXACT = "exact"
    """Identical normalized sets AND identical column count."""
    REORDERED = "reordered"
    """Same names, different order."""
    ADDITIVE = "additive"
    """File has all expected names plus extra ones (superset)."""
    SUBSET = "subset"
    """File is missing some expected names (subset)."""
    PARTIAL = "partial"
    """Some overlap, some missing, some extra."""
    DISJOINT = "disjoint"
    """No names in common."""


@dataclass
class FamilyCandidate:
    """One structural family during the clustering phase."""
    family_id: str
    canonical_column_set: frozenset[str]
    """The normalized name set that defines this family."""
    canonical_column_order: tuple[str, ...]
    """Normalized names in their original order (from the first member)."""
    column_count: int
    member_indices: list[int] = field(default_factory=list)
    """Indices into the PhysicalFile list."""


def jaccard(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two sets. Returns 0.0 for empty union."""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def structural_relation(
    file_names: frozenset[str],
    family_names: frozenset[str],
) -> StructuralRelation:
    """Compute the structural relationship of a file's name set vs a family's name set."""
    if file_names == family_names:
        return StructuralRelation.EXACT
    intersection = file_names & family_names
    if not intersection:
        return StructuralRelation.DISJOINT
    if file_names > family_names:
        return StructuralRelation.ADDITIVE
    if file_names < family_names:
        return StructuralRelation.SUBSET
    return StructuralRelation.PARTIAL


@dataclass
class ClusterResult:
    """Output of the clustering pass."""
    families: list[FamilyCandidate]
    file_family_ids: list[str | None]
    """One entry per PhysicalFile. None = could not be clustered (e.g. no columns)."""
    file_relations: list[StructuralRelation | None]
    """Structural relation of each file to its assigned family."""
    file_explanations: list[str]
    """Per-file explanation string for its cluster placement."""


def assign_families(
    normalized_column_lists: list[list[str]],
) -> ClusterResult:
    """
    Assign each file (represented by its normalized column list) to a family.

    Grouping is by EXACT normalized-set identity — deterministic.
    Column order is tracked as a separate signal (reorder detection).

    Parameters:
        normalized_column_lists: one list per file, in file order.

    Returns:
        ClusterResult with family assignments and per-file explanations.
    """
    families: list[FamilyCandidate] = []
    set_to_family: dict[frozenset[str], str] = {}

    file_family_ids: list[str | None] = []
    file_relations: list[StructuralRelation | None] = []
    file_explanations: list[str] = []

    family_counter = 0

    for idx, col_list in enumerate(normalized_column_lists):
        if not col_list:
            file_family_ids.append(None)
            file_relations.append(None)
            file_explanations.append("No columns detected; not assigned to a family.")
            continue

        col_set = frozenset(col_list)
        col_tuple = tuple(col_list)

        if col_set in set_to_family:
            # Exact match → join existing family
            fid = set_to_family[col_set]
            family = next(f for f in families if f.family_id == fid)
            family.member_indices.append(idx)

            relation = StructuralRelation.EXACT
            # Check if order differs from family canonical order
            if col_tuple != family.canonical_column_order:
                relation = StructuralRelation.REORDERED
                explanation = (
                    f"Assigned to family {fid!r}: same {len(col_list)} columns "
                    f"but different order than the canonical member."
                )
            else:
                explanation = (
                    f"Assigned to family {fid!r}: exact structural match "
                    f"({len(col_list)} columns, same order)."
                )

            file_family_ids.append(fid)
            file_relations.append(relation)
            file_explanations.append(explanation)

        else:
            # New family
            family_counter += 1
            fid = f"F{family_counter:04d}"
            family = FamilyCandidate(
                family_id=fid,
                canonical_column_set=col_set,
                canonical_column_order=col_tuple,
                column_count=len(col_list),
                member_indices=[idx],
            )
            families.append(family)
            set_to_family[col_set] = fid

            file_family_ids.append(fid)
            file_relations.append(StructuralRelation.EXACT)
            file_explanations.append(
                f"First member of new family {fid!r} "
                f"({len(col_list)} columns: "
                f"{', '.join(list(col_list)[:5])}"
                f"{'...' if len(col_list) > 5 else ''})."
            )

    return ClusterResult(
        families=families,
        file_family_ids=file_family_ids,
        file_relations=file_relations,
        file_explanations=file_explanations,
    )


def describe_diff(
    file_names: frozenset[str],
    family_names: frozenset[str],
    file_col_order: tuple[str, ...],
    family_col_order: tuple[str, ...],
) -> str:
    """
    Generate a human-readable diff string between a file's column set
    and its family's canonical column set.

    Used to populate the within-family variant descriptions.
    """
    missing = sorted(family_names - file_names)
    extra = sorted(file_names - family_names)
    parts: list[str] = []

    if missing:
        parts.append(f"missing: {missing}")
    if extra:
        parts.append(f"extra: {extra}")
    if not missing and not extra and file_col_order != family_col_order:
        parts.append("same columns, different order")
    if not parts:
        parts.append("exact match")

    return "; ".join(parts)
