# willitload — synthetic test corpus

Four kinds of test data, each under its own parent directory. Generated
deterministically by `generate_corpus.py` (seed 20260718) so it regenerates identically.

## 01_breadth/ — coverage of specific edge cases
One phenomenon per folder, each with a `README.txt` describing intent:
- `encoding_boundary/` — valid UTF-8 with a multibyte char straddling the 64KB and 1MB buffer
  boundaries. **Must NOT be flagged** (false-positive test for the boundary bugs).
- `encoding_zoo/` — UTF-8, UTF-8-BOM, UTF-16LE, Latin-1 (all decodable) + one genuinely
  broken-byte file (must be flagged).
- `delimiter_variety/` — comma/tab/pipe/semicolon, same logical schema.
- `header_ambiguity/` — header present / absent / numeric-header-that-isn't.
- `type_edge/` — leading zeros, IDs with trailing letters, int/text mixes, bools, ISO dates.
- `ragged_and_truncated/` — ragged rows, trailing TOTAL row, truncated final row, unescaped comma.
- `format_zoo/` — real CSV/JSONL/JSON/Parquet/SQLite + a `.csv` that's actually JSONL.
- `archive_health/` — healthy gzip, truncated (corrupt) gzip, gzip with bad inner bytes,
  normal zip, nested zip.
- `baseline_beside_data/` — the footgun: `baseline.schema` + README + dotfile sitting in the
  data folder. The scanner must NOT profile them as data or form phantom families.

## 02_demo/ — a realistic story
`acme_daily_orders/` — 10 clean daily vendor files, then day 11 adds a column, day 12 renames
one, day 13 corrupts a price with `$`, day 14 is truncated. Baseline (`expected.schema`) sits
*outside* the data folder (correct usage). Good material for a README/blog demo.
```
willitload check 02_demo/acme_daily_orders --against 02_demo/expected.schema --align name
```

## 03_scale/ — performance + needle-in-haystack
`wide_feed/` — 1,200 files, 1,196 clean + 4 needles (extra col @137, missing col @512,
type drift @888, renamed col @1050). Baseline sits outside. Tests the sample-then-confirm
path and the "1000 files in seconds" claim.

## 04_blind/ — adversarial (no inline hints)
Six folders (`case_alpha`..`case_zeta`) with problems seeded from independent logic. The
truth lives ONLY in `ANSWER_KEY.md` — run willitload first, record verdicts, THEN compare to
score precision (false positives) and recall (misses). Includes the false-positive trap
(alpha), mid-row column insert (beta), same-type swap (gamma), corrupt gzip (delta), type
drift (epsilon), and whole-folder uniform drift that only a baseline can catch (zeta).

## Portability note
`.gitattributes` forces LF on all text fixtures. Do NOT commit CRLF fixtures — byte-size
snapshots computed on CRLF won't match on LF (this is a real bug the corpus is meant to help
avoid).
