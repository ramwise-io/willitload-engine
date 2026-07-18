"""
willitload.tier0.format_detect — Format detection by content, not extension.

Protocol (magic-bytes-first, content-disambiguation second):
  1. Magic byte signatures (PAR1, SQLite, ZIP/xlsx, gzip, XML)
  2. JSON vs delimited disambiguation on plain-text files
  3. Extension-assisted hint for ambiguous cases (low confidence)

Never trusts the extension alone. Extension mismatch is reported but never
used to override magic-byte detection.

Returned format names are lowercase short strings:
  'csv', 'parquet', 'json', 'jsonl', 'sqlite', 'xml', 'zip', 'gzip',
  'excel', 'tsv', 'fixed-width', 'unknown'
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Magic byte signatures (checked first — deterministic)
# ---------------------------------------------------------------------------

_MAGIC: list[tuple[bytes, str]] = [
    (b"PAR1",                  "parquet"),
    (b"SQLite format 3\x00",  "sqlite"),
    (b"\x1f\x8b",             "gzip"),
    (b"PK\x03\x04",           "zip"),      # ZIP / XLSX / XLSM / ODT etc.
    (b"PK\x05\x06",           "zip"),      # empty ZIP
    (b"PK\x07\x08",           "zip"),      # spanned ZIP
    (b"<?xml",                "xml"),
    (b"\xef\xbb\xbf<?xml",   "xml"),      # UTF-8 BOM + XML
    (b"\x00\x00\xfe\xff<?xml","xml"),
    (b"\xfe\xff<?xml",        "xml"),
    (b"ARROW1",               "arrow"),    # Apache Arrow IPC
    (b"ORC",                  "orc"),
    (b"\x4f\x62\x6a\x01",    "avro"),    # Avro object container
]

_SAMPLE_BYTES = 1024  # enough for magic detection + content sniff


def _read_magic(path: Path, n: int = _SAMPLE_BYTES) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _is_excel(path: Path, sample: bytes) -> bool:
    """
    XLSX is a ZIP file with a specific internal structure.
    We detect it by checking ZIP magic + peeking at namelist.
    """
    if not sample.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            return any(n.startswith("xl/") for n in names)
    except Exception:
        return False


def _detect_text_format(sample: bytes, encoding: str) -> tuple[str, int]:
    """
    Disambiguate text-based formats (JSON, JSONL, CSV, TSV, fixed-width).
    Returns (format_name, confidence).
    """
    try:
        text = sample.decode(encoding, errors="replace").strip()
    except Exception:
        return ("unknown", 0)

    if not text:
        return ("unknown", 0)

    first_char = text[0]

    # JSON object or array
    if first_char in ("{", "["):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if all(ln.startswith("{") for ln in lines[:5]):
            return ("jsonl", 2)
        return ("json", 2)

    # Delimited heuristic: candidate-frequency-with-consistent-column-count
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ("unknown", 0)

    candidates = ["\t", "|", ";", ","]
    scores: dict[str, float] = {}
    for delim in candidates:
        counts = [line.count(delim) for line in lines[:20]]
        if not any(c > 0 for c in counts):
            continue
        mode_count = max(set(counts), key=counts.count)
        if mode_count == 0:
            continue
        consistency = sum(1 for c in counts if c == mode_count) / len(counts)
        scores[delim] = consistency * mode_count  # weight consistency + density

    if scores:
        best = max(scores, key=lambda k: scores[k])
        if best == "\t":
            return ("tsv", 2)
        return ("csv", 2)

    # No delimiters found — check if it looks fixed-width
    if lines and len(set(len(ln) for ln in lines[:20])) <= 2:
        return ("fixed-width", 1)

    return ("text", 1)


def detect_format(
    path: Path,
    sample: bytes | None = None,
    encoding: str = "utf-8",
) -> tuple[str, int]:
    """
    Detect the file format from content (magic bytes first, then text analysis).

    Returns:
        (format_name, confidence)
    """
    if sample is None:
        sample = _read_magic(path)
    if not sample:
        return ("unknown", 0)

    # Magic byte pass
    for magic, fmt in _MAGIC:
        if sample.startswith(magic):
            if fmt == "zip":
                # Disambiguate: Excel is a ZIP
                if _is_excel(path, sample):
                    return ("excel", 2)
                return ("zip", 2)
            return (fmt, 2)

    # Plain text: JSON / delimited / fixed-width
    return _detect_text_format(sample, encoding)
