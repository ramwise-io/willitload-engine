"""
willitload.tier1.canonicalize — Safe, lossless, visible, toggleable name normalization.

The ONLY transformations applied to column names before clustering:
  1. Strip leading/trailing whitespace
  2. Case-fold (lowercase)
  3. Collapse interior whitespace runs to a single space

NOTHING ELSE. No singular/plural folding, no aggressive separator-stripping
that could merge genuinely distinct names. Those cross the line into guessing.

Both raw (as reported) and normalized (as clustered-on) names are preserved.
Each normalization step is recorded so grouping is fully explainable.

The pipeline is toggleable: each step can be disabled via CanonicalizationConfig.
Disabling a step means that step's output equals its input (no transformation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CanonicalizationConfig:
    """Controls which normalization steps are active."""
    strip_whitespace: bool = True
    case_fold: bool = True
    collapse_interior_whitespace: bool = True


@dataclass
class CanonicalizationTrace:
    """
    Records the effect of each normalization step on one column name.
    Enables per-file explanation of why names were clustered together.
    """
    raw: str
    after_strip: str
    after_case_fold: str
    after_collapse: str

    @property
    def normalized(self) -> str:
        return self.after_collapse

    @property
    def was_transformed(self) -> bool:
        return self.raw != self.normalized

    def describe(self) -> str:
        """Human-readable description of what changed."""
        parts = []
        if self.raw != self.after_strip:
            parts.append(f"whitespace stripped: {self.raw!r} → {self.after_strip!r}")
        if self.after_strip != self.after_case_fold:
            parts.append(f"case-folded: {self.after_strip!r} → {self.after_case_fold!r}")
        if self.after_case_fold != self.after_collapse:
            parts.append(f"whitespace collapsed: {self.after_case_fold!r} → {self.after_collapse!r}")
        if not parts:
            return f"unchanged: {self.raw!r}"
        return "; ".join(parts)


_INTERIOR_WS = re.compile(r"\s+")


def canonicalize_name(
    raw: str,
    config: CanonicalizationConfig | None = None,
) -> CanonicalizationTrace:
    """
    Apply the three-step canonicalization pipeline to a single column name.
    Returns a CanonicalizationTrace with the result of each step.
    """
    if config is None:
        config = CanonicalizationConfig()

    after_strip = raw.strip() if config.strip_whitespace else raw
    after_case_fold = after_strip.lower() if config.case_fold else after_strip
    after_collapse = (
        _INTERIOR_WS.sub(" ", after_case_fold)
        if config.collapse_interior_whitespace
        else after_case_fold
    )

    return CanonicalizationTrace(
        raw=raw,
        after_strip=after_strip,
        after_case_fold=after_case_fold,
        after_collapse=after_collapse,
    )


def canonicalize_names(
    raw_names: list[str],
    config: CanonicalizationConfig | None = None,
) -> tuple[list[str], list[CanonicalizationTrace]]:
    """
    Canonicalize a list of column names.

    Returns:
        (normalized_names, traces)
        normalized_names: list of normalized strings (in same order as raw_names)
        traces: one CanonicalizationTrace per input name
    """
    traces = [canonicalize_name(n, config) for n in raw_names]
    normalized = [t.normalized for t in traces]
    return normalized, traces
