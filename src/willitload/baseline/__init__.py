# src/willitload/baseline/__init__.py
"""Baseline front-doors — all normalize to the same internal fingerprint."""

from willitload.baseline.fingerprint import BaselineFingerprint, BaselineColumn
from willitload.baseline.flat import parse_flat_schema
from willitload.baseline.from_json import parse_from_scan_json
from willitload.baseline.golden import parse_golden_file

__all__ = [
    "BaselineFingerprint",
    "BaselineColumn",
    "parse_flat_schema",
    "parse_from_scan_json",
    "parse_golden_file",
]
