"""
willitload.tier0.encoding — Deterministic encoding detection (no chardet).

Protocol (BOM-first, then decode-verify, then Latin-1 last-resort):
  1. BOM sniff: UTF-32 LE/BE, UTF-16 LE/BE, UTF-8 BOM
  2. Try UTF-8 strict decode on sample
  3. Try UTF-16 LE/BE via null-byte pattern analysis on sample
  4. Fall through to Latin-1 (always succeeds; raises ENCODING_FALLBACK finding)

No probabilistic guessing. A file either decodes cleanly under a known encoding,
or it is flagged as degraded with ENCODING_FALLBACK. "Guessing" is out of scope
per the no-guessing principle that underpins the tool's trust story.
"""

from __future__ import annotations

SAMPLE_BYTES = 64 * 1024  # 64 KB is sufficient for BOM + decode verification

# BOM signatures in detection priority order
_BOMS: list[tuple[bytes, str]] = [
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe",         "utf-16-le"),
    (b"\xfe\xff",         "utf-16-be"),
    (b"\xef\xbb\xbf",     "utf-8-sig"),  # UTF-8 with BOM
]


def _read_sample(path: str, n: int = SAMPLE_BYTES) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)


def _has_utf16_null_pattern(sample: bytes, byte_order: str) -> bool:
    """
    Heuristic null-byte pattern check for UTF-16 without a BOM.
    UTF-16 LE: every second byte starting from index 0 is 0x00 for ASCII range.
    UTF-16 BE: every second byte starting from index 1 is 0x00 for ASCII range.
    We check the first 256 bytes; require at least 80% match to assert.
    """
    if len(sample) < 4:
        return False
    check_slice = sample[:256]
    if byte_order == "le":
        nulls = sum(1 for i in range(1, len(check_slice), 2) if check_slice[i] == 0)
        total = len(check_slice) // 2
    else:
        nulls = sum(1 for i in range(0, len(check_slice) - 1, 2) if check_slice[i] == 0)
        total = len(check_slice) // 2
    return total > 0 and (nulls / total) >= 0.80


def detect_encoding(path_or_sample: str | bytes) -> tuple[str, bool]:
    """
    Deterministically detect the encoding of a file.

    Parameters:
        path_or_sample: absolute path string OR pre-read bytes sample.

    Returns:
        (encoding_name, is_fallback)
    """
    if isinstance(path_or_sample, bytes):
        sample = path_or_sample
    else:
        try:
            sample = _read_sample(path_or_sample)
        except OSError:
            return ("latin-1", True)

    # Step 1: BOM sniff
    for bom, encoding in _BOMS:
        if sample.startswith(bom):
            return (encoding, False)

    # Step 2: Try UTF-8 strict decode
    try:
        sample.decode("utf-8", errors="strict")
        return ("utf-8", False)
    except UnicodeDecodeError as e:
        if len(sample) >= SAMPLE_BYTES and len(sample) - e.start <= 4:
            try:
                sample[:e.start].decode("utf-8", errors="strict")
                return ("utf-8", False)
            except UnicodeDecodeError:
                pass
    except ValueError:
        pass

    # Step 3: UTF-16 LE/BE null-byte pattern (without BOM)
    if _has_utf16_null_pattern(sample, "le"):
        try:
            sample.decode("utf-16-le", errors="strict")
            return ("utf-16-le", False)
        except UnicodeDecodeError as e:
            if len(sample) >= SAMPLE_BYTES and len(sample) - e.start <= 4:
                try:
                    sample[:e.start].decode("utf-16-le", errors="strict")
                    return ("utf-16-le", False)
                except UnicodeDecodeError:
                    pass
        except ValueError:
            pass

    if _has_utf16_null_pattern(sample, "be"):
        try:
            sample.decode("utf-16-be", errors="strict")
            return ("utf-16-be", False)
        except UnicodeDecodeError as e:
            if len(sample) >= SAMPLE_BYTES and len(sample) - e.start <= 4:
                try:
                    sample[:e.start].decode("utf-16-be", errors="strict")
                    return ("utf-16-be", False)
                except UnicodeDecodeError:
                    pass
        except ValueError:
            pass

    # Step 4: Latin-1 last resort (always succeeds at the byte level)
    # No decode call needed — Latin-1 always succeeds. Caller raises ENCODING_FALLBACK.
    return ("latin-1", True)
