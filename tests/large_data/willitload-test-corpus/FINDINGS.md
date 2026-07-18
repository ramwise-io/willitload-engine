# What this corpus surfaced when run against willitload (0.1.0)

Run on Linux, 2026-07-18. These are observations from actually executing the
engine against the corpus — the reason a *second, independent* dataset is worth
having.

## Passed cleanly (good)
- **04_blind/case_alpha** — 9 valid files with accented names: ALL GOLDEN. No
  encoding false-positive. ✓
- **04_blind/case_beta** — mid-row inserted column correctly isolated as a
  separate family / broken file. ✓
- **04_blind/case_delta** — truncated gzip correctly REFUSED as CORRUPT_ARCHIVE. ✓
- **04_blind/case_gamma** — same-type swap: conforms in NAME mode (correct),
  WARN in POSITION mode via COLUMN_NAME_MISMATCH (correct physics-limit behavior). ✓
- **04_blind/case_zeta** — whole-folder uniform drift (all missing `revenue`):
  invisible to `scan`, correctly ALL BROKEN under `check` vs baseline. ✓ Validates
  the "baseline required for uniform drift" design.

## Surfaced issues (worth a decision or fix)
1. **int→text classified as WIDENING (WARN), not BREAKING (ERROR).**
   `types.py` treats `(INT, TEXT)` and `(DECIMAL, TEXT)` as widening ("text can
   hold ints"). Consequence: case_epsilon's text-id-where-int-expected is WARN, so
   the file still lands in GOLDEN/conforms. But numeric→text is the exact silent-
   corruption the blog post headlines (ID column silently became text). Recommend
   reclassifying numeric→text as BREAKING/ERROR for this tool's purpose. (Design
   decision, not a bug — but a mismatch between mission and behavior.)

2. **Performance target missed.** 1,200 files scanned in ~10s (~120 files/s), i.e.
   ~8s per 1,000 vs the spec's <5s/1,000 target. ~2x off. Likely per-file overhead
   (DuckDB connection reuse / sample-read batching). 03_scale/wide_feed is a
   reproducible benchmark for this.

3. **check() requires AlignmentMode enum, doesn't coerce strings.** Passing
   mode="name" raises AttributeError deep in to_json (str has no .value). Minor API
   robustness gap — accept strings or validate at the boundary with a clear error.

4. (From the separate code review, reproduced here) **baseline.schema / README /
   dotfiles in a scanned folder get profiled as data.** See
   01_breadth/baseline_beside_data/ — the scanner should not form phantom families
   from non-data files, and `check` should exclude the baseline path.
