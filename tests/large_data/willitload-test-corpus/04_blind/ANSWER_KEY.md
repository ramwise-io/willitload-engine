# ANSWER KEY — 04_blind

Kept separate on purpose. Run willitload against each folder, record its
verdicts, THEN compare here. Score precision (false positives) and recall
(missed problems).

## case_alpha
TRUTH: all 9 files conform. Same schema, valid UTF-8 throughout.
Trap: accented names may tempt an encoding false-positive. Expect: ALL GOLDEN.

## case_beta
TRUTH: day7.csv has an EXTRA column 'region' inserted mid-row.
Others (day1-6) conform. Expect: day7 BROKEN (extra col), rest GOLDEN.

## case_gamma
TRUTH: b.csv swaps amount<->tax (same type, decimal).
In NAME mode: a/b/c all conform (names bind, swap is a non-event).
In POSITION mode vs a name-bearing baseline: b.csv should WARN (possible swap).
This is the physics-limit case — no ERROR is possible, only a WARN via names.

## case_delta
TRUTH: feed_03.csv.gz is a truncated/corrupt gzip.
feed_01/02 are healthy. Expect: feed_03 BROKEN (CORRUPT_ARCHIVE), rest GOLDEN.

## case_epsilon
TRUTH: p6.csv has a text id ('ABC-99') where others are int.
Expect (vs int-id baseline): p6 BROKEN (type mismatch on id), rest GOLDEN.

## case_zeta
TRUTH: ALL 7 files are internally consistent with each other but
ALL are missing 'revenue' vs the expected baseline. Outlier detection alone
(no baseline) would call these clean; only a baseline check catches the
whole-folder drift. Expect (vs baseline w/ revenue): ALL 7 BROKEN (missing revenue).
