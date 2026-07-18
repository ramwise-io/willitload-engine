# willitload — Engine Specification (Fileset Structural Pre-Flight for Bulk Loads)

**Status:** v1 scope, design-locked
**Nature:** Stateless, deterministic, local, CLI-first engine. No AI/ML. No network. No persisted state.

---

## 1. Purpose & one-sentence definition

Point the tool at a set of data files that a **bulk load** will consume as one logical
dataset, and get back a **named partition of the fileset into conforming ("golden") and
non-conforming ("broken") files**, where each broken file is annotated with its **exact
structural deviation** and a **mode-appropriate severity**.

> A bulk load is only as good as its worst-conforming file. The tool finds that file —
> by name, before the load does.

The deliverable is the **per-file verdict list**. Everything else in the engine exists to
produce that list correctly and fast across thousands of files.

---

## 2. What this is and is NOT

### It IS
- A **structural** describer and validator of filesets (names, types, counts, positions).
- A **pre-flight** check for **bulk loads** (many files → one dataset).
- **Deterministic**: same input → same output. Every finding is explainable.
- **Loader-agnostic**: reports structural facts; the user maps them to their loader's tolerance.
- **Stateless**: baseline is an *input at scan time*, never stored or tracked.

### It is NOT
- Not a data-mover / ETL tool. It never transforms or loads data.
- Not a data-quality tool. It never judges *values* (validity, ranges, enums, uniqueness, referential rules, semantic correctness).
- Not a live connector to warehouses/systems. It never reads the user's live schema over a connection.
- Not a monitor. No baseline store, no time-series, no tracking of the user's system.
- Not a merge/union simulator. It does not predict or rehearse what a loader will produce.
- Not for sequential single-file-append (pandas loop) topologies as a distinct mode — see §11.

### The hard boundary (the "quality line")
The test for any candidate feature:
> **Can this be answered from the *shape/representation* of the data alone, without knowing
> what the data is *supposed to mean*?**
- **Yes** → in scope (structural).
- **No** (needs a rule, reference set, range, or domain meaning) → out of scope (quality).

The engine describes and compares structure. It **cannot** confirm that values under a
column *mean* what the column claims — that is beyond deterministic reach and is disclosed,
not chased.

---

## 3. Distribution & architecture

### 3.1 Distribution decision: **Python package over DuckDB** (primary path)

Ship as a pip-installable Python package (library + CLI from one artifact), NOT a standalone
binary. Rationale, for the target enterprise-DE audience:
- **Native to the buyer's world.** The DE already has Python and already writes
  pandas/PySpark/glue code. The tool sits *inside* their pre-flight workflow:
  `pip install willitload`, `from willitload import check`, call it in the same script that fires the
  bulk load, or in the same CI job. A binary is a foreign object in a Python pipeline; a
  package is a native citizen.
- **CI is the high-value surface.** The recurring drift-gate (`check` on every PR / pre-load)
  is far lower friction as a line in `requirements.txt` than provisioning a binary into a CI
  image.
- **Free library API.** `from willitload import scan, check` returns typed result objects the DE
  can branch on / quarantine-with / integrate — not just parsed CLI JSON. One package yields
  library **and** CLI (via a console entry point).
- **Enterprise dependency vetting is a paved road.** A package (public PyPI or a private index
  — Artifactory / Azure Artifacts) fits existing vendoring/vetting. An unsigned binary trips
  SmartScreen/Gatekeeper and security review. Publishing to a private index is the *same*
  `twine upload`, just a different destination — enterprise and packaging compose cleanly.

**The trade being accepted:** the "single static binary, no runtime, copy-to-any-server"
property is given up. For a pre-flight-in-their-workflow tool that's the right trade — optimize
for "runs where the DE already works," not "runs anywhere with zero deps." (The original
standalone-server-job framing is out of scope; see §11 — we target bulk-load pre-flight.)

**Why this stays fast (the load-bearing point, given §17):** the heavy lifting is delegated to
**DuckDB**, a C++ engine called from Python at near-native speed and shipped as a normal wheel
(`pip install duckdb` — no compiler on the user's machine). Globbing, CSV/JSON/Parquet reading,
type inference, and sampling aggregations run **inside DuckDB**, not in Python loops. Python is
only the **orchestration** layer: expand the glob (DuckDB), fingerprint each file (DuckDB reads/
types), compare fingerprints and assemble the partition (cheap, pure Python even across
thousands of files). The parts that must be fast are the parts DuckDB already handles in C++.

**The discipline that makes it work (non-negotiable):** DuckDB does the file I/O and heavy
compute; **Python only orchestrates.** The failure mode is NOT "Python is slow" — it is
"someone writes file scanning as Python loops instead of pushing it into DuckDB." The
Python-slow parts are the specialized parsers (Excel/XML/ZIP/encoding sniffing on weird files)
— minority file types in a bulk-load set (mostly CSV/Parquet/JSON = DuckDB's home turf), and
bounded by sample-then-confirm.

**Escape hatch (only if profiling proves the orchestration layer — not DuckDB — is the
bottleneck):** rebuild the core in Rust and ship as a Python wheel via **PyO3 / maturin**
(the pydantic-core / polars / ruff pattern). Keeps `pip install` + Python API + native speed.
Cost: compiled wheels become a **per-OS/arch matrix** built in CI (`cibuildwheel`) instead of
one pure-Python wheel that runs everywhere. Do NOT start here — start pure-Python-over-DuckDB;
reach for Rust bindings only on measured need.

### 3.2 Layers (build bottom-up)

```
CORE PACKAGE  (src/willitload/core — headless, no I/O opinions, no printing)
  scan → fingerprint → group → diff-against-baseline → typed result
      DuckDB called INTERNALLY as the engine for CSV/JSON/Parquet profiling and
      globbing; specialized Python parsers alongside it (Excel, XML, ZIP, SQLite).
      Core knows nothing about which engine handled which file. Pure-Python
      orchestration; heavy compute pushed into DuckDB.

LIBRARY API  (src/willitload/__init__.py)
  `from willitload import scan, check` → typed result objects. The programmatic surface.

CLI  (src/willitload/cli.py — thin, renderer + console entry point)
  path expression + flags → calls core → emits `--json` (the contract) or
      human-readable text (a renderer over the same JSON). Declared as a console
      entry point in pyproject.toml, so `willitload ...` works from the shell.

FRONTENDS  (future, optional, bolt-on anywhere)
  web / desktop / CI annotation — all consume the JSON or the library API.
```

### 3.3 Packaging mechanics (reference — first publish ≈ an afternoon, releases ≈ 3 commands)

- **Layout:** `pyproject.toml` at root (name, version, dependencies incl. `duckdb`, supported
  Python versions, **console entry point** that maps `willitload` → the CLI function); code under
  `src/willitload/`; `tests/`.
- **Build:** `python -m build` → produces wheel (`.whl`) + sdist (`.tar.gz`) in `dist/`.
  No compilation for pure Python.
- **Publish:** `twine upload dist/*` → PyPI (practice on **TestPyPI** first). Private index
  = same command, different URL.
- **Install (user):** `pip install willitload` — pip pulls the wheel and declared deps (DuckDB
  included, transparently) automatically.
- **Versioning:** semantic (`major.minor.patch`). Because the JSON is the contract (below),
  **contract stability across minor versions is a real promise** the moment you publish —
  don't break JSON shape or function signatures in a minor bump.
- **Cross-platform:** pure-Python = one wheel runs everywhere. (Only the Rust escape hatch
  introduces the per-OS/arch wheel matrix.)

**Contract rule (non-negotiable):** the `--json` output AND the library's typed result objects
are the API. Human text is just another renderer. Never let print-formatting become the
contract. The path expression and baseline are passed as **data/parameters**, never
interpolated into a SQL string handed to DuckDB (avoids quoting footguns and injection-shaped
bugs).

---

## 4. Input contract & baseline expectations

The engine accepts a **path expression** as primary input (not just a bare directory).

### 4.1 Path expression / globbing
- Accept directory **or** glob. Wildcard vocabulary: `*` (within a path segment),
  `**` (recursive across directories), `?` (single char) — same vocabulary Spark/Glue/
  PolyBase devs already use, so no translation.
- Expand via DuckDB's `glob()` table function to a **file list**, then inspect each file
  independently. **Do NOT** hand the glob to a reader and let it union — that is the load's
  job and the thing that silently misbehaves; the tool inspects per-file.
- Ceilings apply to the **expanded** set (see §4.3).
- **Zero-match is a finding**, not an error/exception.
- Known constraint (future/S3): the `?` wildcard is not reliable over S3 (HTTP encoding);
  local + HTTP(S) are fine. v1 is local-first; detect S3 paths and warn if `?` used.

### 4.2 Scale bounds (every limit: explicit default, overridable flag, produces a stated finding when hit)
- **Per-file size:** sample-based profiling; state the sample policy; a huge file is
  profiled from a sample, never force-read whole unless an anomaly is being *confirmed*.
- **File count:** default ceiling; beyond it, warn + require `--limit`/`--force`.
  "Scanned N of M (limit reached; use --limit to raise)" — never a silent stop.
- **Recursion depth:** capped; **follow-symlinks OFF by default** (loop/hang protection).
- **Total bytes scanned:** capped (protects against secretly-huge mounts).
- **Archive nesting depth:** decompress to a stated depth (1, maybe 2); deeper → finding
  ("nested archive beyond scan depth"); cap decompressed size (zip-bomb protection);
  encrypted archive → catalogued as "encrypted, not inspected."

### 4.3 Access posture
- **Read-only, always.** Never write into the scanned folder.
- Permission-denied on a file → **finding**, not crash.
- Network/mounted drives allowed but flagged "slow path"; byte-ceiling protects.
- File changing mid-scan: single-pass, stateless — report what was seen; do not attempt
  cross-file consistency of a moving target.

### 4.4 File accounting (first-class output)
Every file lands in exactly one bucket, and the buckets reconcile:
```
files_seen = profiled + degraded + refused
```
- **recognized-and-profiled** — read and fingerprinted.
- **recognized-but-degraded** — partial/malformed but partially readable.
- **unrecognized-catalogued-only** — physical fingerprint known, not structurally profiled.
- **unreadable/refused** — permission, corrupt, encrypted.
Nothing is ever silently dropped; the reconciliation is itself a trust feature.

### 4.5 Baseline (for `check` mode) — an INPUT, never stored state
The baseline is a thing the user *points at* at scan time, exactly like the folder. Diff
against it, report, forget. This keeps drift-detection fully stateless (the "monitoring is a
different animal" concern never enters — we do NOT track the user's system live).

**Accepted baseline front-doors (all normalize to the same internal fingerprint):**
1. **Dumb flat schema file** — one column per line, `name<sep>type`. Primary format.
2. **Prior scan's own JSON output** — the tool's own emitted fingerprint, round-tripped as
   input. Free (own format), git-committable as a CI fixture.
3. **Golden sample file** — point at an actual file declared correct; fingerprint it with
   the scanner and use that as the baseline. Free (reuses scanner). This is also how
   "match this Parquet/Avro schema" is supported without adding schema-format parsers.

**Explicitly rejected baseline sources (with reason):**
- Live warehouse/table connection → that's tracking their system + connectors + state. The
  user extracts a DDL themselves. **Rejected.**
- dbt schema.yml, JSON Schema, Avro `.avsc`, Iceberg/Delta schema as first-class formats →
  the thin end of a format-completeness treadmill. Covered indirectly via golden-file
  (point at a file of that type). **Rejected as explicit formats;** may be added later as
  thin parsers that normalize to the same fingerprint IF demand proves out.
- DDL (`CREATE TABLE`) → **deferred, not rejected.** Highest-value (it's the target
  contract) but the only source with real parsing cost (SQL dialects). Gate behind "did the
  free baselines prove the loop." When added: parse name/type/order only, normalize types to
  the coarse classes in §6, treat dialect type names as aliases.

**Baseline is dumb and carries no behavior.** Alignment mode and extra-column policy are
**CLI flags on `check`**, NOT directives in the baseline file. The same baseline can be
checked strictly today and loosely tomorrow without editing it.

---

## 5. The two alignment modes (reverse-engineered from how bulk loads bind)

The tool's checks mirror exactly what each load type deterministically binds on. It never
promises more certainty than the load itself has.

### 5.1 Name mode (default; `--align name`)
- **Load contract mirrored:** loads that bind column→column by **name** (Spark name-union,
  ADF byName mapping, PolyBase-by-name, Fabric name-mapped sinks, pandas by-name concat).
- **Requires names** — so requires header-present files (see §7).
- **Deterministically verifies:** expected columns present, names match (after safe
  canonicalization), types compatible.
- **Detects:** additive (new column), missing (dropped column), rename/misspelling
  (as a name mismatch), incompatible type change — all fatal-grade (they break/mismap the
  name-bound load).
- **Does NOT promise:** that data under a correctly-named column *means* what the name says
  (the value-meaning limit — disclosed).
- **Baseline:** dumb `name,type` list is sufficient.

### 5.2 Position mode (`--align position`)
- **Load contract mirrored:** loads that bind column→column by **ordinal position**
  (headerless CSV against a declared schema, fixed-width, ordinal `COPY INTO`, external
  tables by ordinal, pandas by-position concat).
- **Works with or without headers.** The user may deliberately run positional on
  header'd files because their loader ignores the header and binds ordinally. Header
  presence and alignment are **orthogonal axes** — see §7.
- **Deterministically verifies:** column **count** matches; **type-at-each-position** matches.
- **Detects reliably:** addition and removal (count change); swaps **only when the swapped
  columns differ in type** (type-at-position changes).
- **Blind to:** same-type swaps (physics limit — positional binding has no identity signal
  beyond position; the actual positional *load* is equally blind). Disclosed, not chased.
- **Baseline:**
  - `type`-only ordered list → catches count + type-at-position drift. Swap-blind.
  - **Optional `name,type` ordered list** → additionally compares the file's header-name-at-
    position against the baseline's expected-name-at-position. This catches same-type swaps
    **deterministically** (comparing two declared names, not inferring from content) — but
    only as a **warning** (see §5.4), because the positional load won't fail on it.

### 5.3 The impossible quadrant
**Headerless + name mode = impossible by definition** — a name check compares names; a
headerless file has none. If a name-mode run encounters a headerless file, that file is a
**reported anomaly** ("no header; cannot bind by name — flagged"), never silently switched
to position mode and never dropped.

### 5.4 Severity rule (single rule, applied everywhere)
> **Severity = does the mismatched attribute match what the declared load binds on?**
- Mismatched attribute IS load-binding → **fatal/ERROR** (the load will actually break or
  corrupt on it).
- Mismatched attribute is non-binding but suggestive → **warning/WARN** (the load tolerates
  it, but it hints at a problem the load can't see).

Consequences:
- Name mismatch in **name mode** → ERROR (name is what binds).
- Name mismatch in **position mode** (names present in file and/or baseline) → WARN
  (position binds; name disagreement flags a *possible* swap the positional load is blind to
  — never fatal, because the load itself won't die on it).
- Same physical finding → same `reason_code`; **severity is a projection of
  (reason_code, alignment_mode)**. Keeps the diff engine single-shaped; severity is a thin
  function, not a branch in the comparison logic.

### 5.5 Un-verdicted name observation (position mode, headers present)
When position mode runs on header'd files but the baseline carries no names, the tool still
**surfaces the header names as observations** ("position 2 is named `tax`") without a
verdict — exposing the alignment it's ignoring so the user's eye can catch a swap. Expose
the alignment; withhold the verdict.

---

## 6. Type vocabulary (the one place `type` must be pinned)

Coarse, closed class set — what the engine can verify from samples and what structural
equivalence actually needs. NOT precise SQL types.

**Classes:** `int`, `decimal`, `bool`, `date`, `timestamp`, `text`, `blob`, and `*`/`any`
(present-but-type-unconstrained).

**Alias normalization** (fixed alias→class map, applied on read of baseline AND on type
inference; cheap, not dialect parsing). Examples:
- `int` ← integer, int64, bigint, smallint, long
- `decimal` ← float, double, numeric, number, real, money
- `text` ← string, varchar, char, nvarchar, str
- `timestamp` ← datetime
- `date` ← date
- `bool` ← boolean
(The alias map is also the near-free on-ramp to future DDL ingestion: DDL type names are
just more aliases mapping to the same classes.)

**Type-compatibility lattice** (structural, no loader semantics) for cross-file / vs-baseline
type differences:
- identical
- widening-compatible (e.g. int→decimal, narrower→wider text)
- breaking (e.g. decimal→text, date→int)

---

## 7. Header presence — a detected FILE PROPERTY, orthogonal to alignment

- Header presence is **auto-detected per file** (does row 0's type-profile differ from the
  rows below?), with a manual override for ambiguous cases. Better than a global
  "skip first row" flag because a bulk fileset can be **mixed**; a single global flag would
  be wrong for part of the set. Report disagreement as an oddity ("3,998 have headers, 2 don't").
- Header presence and alignment are **independent axes**. A header'd file can be loaded
  positionally; a headerless file cannot be loaded by name.

**The full matrix:**

| Files | Align | Baseline | Behavior |
|---|---|---|---|
| header'd | name | `name,type` | check by name; catches add/drop/rename/retype (ERROR-grade) |
| header'd | position | `type`-only | check count + type-at-position; header names surfaced as observation; swap-blind (disclosed) |
| header'd | position | `name,type` | check count + type-at-position (load conformance) **and** header-name-at-position vs baseline → catches same-type swaps as **WARN** (best case) |
| headerless | position | `type`-only or `name,type` | check count + type-at-position; no names to observe; swap-blind (disclosed) |
| headerless | name | — | **impossible** → reported anomaly |

- **Header-present + position** is the *most valuable* configuration: positional binding
  (matches the loader) + name observation (catches what the loader misses).

---

## 8. Tier 0 — Acquisition & physical resolution (prerequisite; must precede header reading)

Skipping this makes every downstream tier silently wrong. All byte-level, deterministic.

- Path-expression expansion (§4.1) with ceilings on the expanded set.
- **Format-by-content, not by extension:** magic bytes (`PAR1` Parquet, `SQLite format 3`,
  ZIP/gzip, XLSX=zip+content), JSON vs delimited disambiguation.
- **Encoding detection:** BOM sniff → decode-and-verify (UTF-8 → UTF-16LE/BE by null-pattern
  → Latin-1); record which succeeded; flag files that only decode under fallback.
- **Compression detection** and transparent handling where DuckDB reads it natively.
- **Delimiter inference:** candidate-frequency-with-consistent-column-count method (not
  most-frequent-char).
- **Quote-style / escape detection.**
- **Newline convention** (`\n` / `\r\n` / `\r`).
- **Size vs siblings** (a file much smaller than its family → truncation signal; arithmetic).
- **Archive membership** (files inside a ZIP tagged with container; bounded nesting depth).
- **Format-specific header/schema access, normalized to a common column-set representation:**
  CSV header row; Parquet footer schema; Excel per-sheet header + sheet enumeration; SQLite
  per-table schema from `sqlite_master`; JSON/JSONL leaf-path set; XML element-hierarchy path
  set; fixed-width inferred field boundaries.
- **File bucketing / accounting** (§4.4).

---

## 9. Tier 1 — Header/structure clustering (no values read)

**Extraction & normalization:**
- Extract column-name set per file (format-appropriate source from Tier 0).
- **Header-presence detection** (row-0 type-profile vs below); for headerless formats,
  synthesize positional identifiers so they can still cluster among themselves.
- **Canonicalization pipeline (safe, lossless, VISIBLE, TOGGLEABLE):** trim, case-fold,
  whitespace-collapse. This is the ONLY transformation applied. Anything beyond it
  (singular/plural folding, aggressive separator-stripping that could merge genuinely
  distinct names) is OFF by default / out — it edges into guessing. Record each step so
  grouping is explainable. Preserve both raw (reported) and normalized (clustered-on) names.

**Structural signals per file:**
- Column count.
- Normalized column-name set.
- Column order (a *separate* signal from the set: same set, different order is distinct).
- Nested-format equivalents: JSON leaf-path set, XML hierarchy path set, Excel sheet-set +
  per-sheet columns, SQLite table-set + per-table columns.

**Clustering & relationships:**
- Exact-structure grouping (identical normalized set + count).
- Graded overlap via column-set **Jaccard** (shared/union) — "28 of 30 match" expressible.
- Structural ladder (name-only): exact / reordered / additive (superset) / subset (missing) /
  partial-overlap / disjoint.
- **Conservative bias:** prefer "these are separate, here's the difference" over confident
  over-merging. One confident wrong grouping destroys trust. Bias toward *showing the diff*.
- Cluster assignment per file + **confidence = count of independent agreeing signals**
  (honest, not a made-up probability). Name-only agreement = low; name+type = higher.
- **Per-file explanation string** for its placement (the moat feature).

**Intra-file structural anomalies (pure structure, no types needed — fold in here):**
- **Ragged rows** — rows whose column count deviates from the file's mode; locate which
  row/column drifted (catches unescaped-delimiter shift).
- **Multi-record files** — two+ distinct stable column-count regimes stacked (segment by
  column-count runs).
- **Truncation** — abrupt EOF mid-record / final partial row.
- **Trailing summary/total rows** — final row whose profile breaks the column pattern.

**Tier 1 outputs:** the fileset summary (N files, M families, K variants, counts); family
membership; within-family header variants with specific diffs; header-level outliers;
file-accounting reconciliation; exportable inventory (JSON + text render).

---

## 10. Tier 2 — Type refinement (sampling required)

**Sampling mechanics:**
- Bounded sample per file (first-N + random-N), configurable, applied **only within
  already-formed Tier-1 families** — never blindly across the whole folder.
- **Sample-then-confirm:** grouping/typing may run on the sample, but any **anomaly claim**
  is re-verified against the full file before assertion. (This is also what keeps
  "check thousands of files" pre-flight-fast: expensive full-file confirmation cost scales
  with the number of *suspects*, not the number of files. A 99.9%-clean set clears almost
  for free.)

**Type inference per column:**
- Strict-to-loose ladder: integer → decimal → boolean → date → timestamp → text; record the
  tightest class accepting all sampled non-null values.
- Nullability **observed** (does the sampled column contain nulls) — reported as an
  observation, NOT enforced as a baseline rule (presence-required is structural and
  enforceable; value-not-null edges toward quality → observation only).
- For declared-type formats (Parquet/SQLite), use the declared type; note declared-vs-observed
  divergence if the sample disagrees.

**Type-aware refinement:**
- Split each header-family into type-variants: "same header, same types" vs "same header,
  column X differs in type across files."
- Apply the type-compatibility lattice (§6): identical / widening / breaking.
- **Cross-file type-inference disagreement** (elevated to first-class here; see §11): the
  same column inferring as different classes across files ("id: int in 40 files, text in 3").
  Especially relevant where per-file inference disagreement bites.

**Tier 2 outputs:** type-variant breakdown per family; type-conflict observations across a
family (with counts); refined outliers (files whose *types* deviate though headers matched);
per-observation explanation strings.

**Scope guard:** Tier 2 computes types and (where used) minimal content signals ONLY to sharpen
grouping/verdicts. It never surfaces value judgments, ranges-as-validation, or distribution
analysis for its own sake. **No content sketches are used for drift/baseline detection** (that
was determined to be prediction and was rejected — see §13). Content-shape work, if any, lives
only in stateless descriptive grouping, never in a pass/fail verdict.

---

## 11. Loading topologies — how each maps onto the two modes (no separate modes)

The tool targets **bulk loads** (many files → one dataset; one odd file breaks/corrupts the
whole load). Other topologies **decompose** into the two modes; none needs its own mode.

- **Spark / Databricks default multi-file read** → **name mode with a caveat.** Spark takes
  the *first file's header* then stacks the rest positionally under those names, silently
  *relabeling* mismatches (WARN: "column drift" — loads without error, mismaps). With headers
  on all files this is evaluated exactly as name mode (compare each file's header to the
  reference). Only the failure semantics differ (silent relabel) → captured as a warning
  string. Not a third mode. First-file order is nondeterministic, which makes pre-flight
  *more* valuable.
- **Explicit-schema Spark / declared-schema loads** → position or name against the declared
  baseline.
- **PolyBase / external table by ordinal, fixed-width, ordinal COPY INTO** → position mode.
- **PolyBase / external table by name, ADF byName, Fabric name-mapped** → name mode.
- **Sequential single-file-append in plain Python (pandas loop / concat / vstack)** →
  **NOT a distinct mode.** Decomposes into:
  - by-name concat (pandas default) → **name mode**, with failure semantics = *silent
    NaN-fill* on name drift (new col → NaN back-fill; misspelled col → both columns half-NaN;
    missing col → NaN). Warn accordingly.
  - by-position concat (`header=None`/`.values`/`vstack`) → **position mode.**
  - The one sequential-specific elevation: **cross-file type-inference disagreement**
    (Wrinkle) — already detected in Tier 2; surfaced prominently because it bites hardest here.
  - **Do NOT** model any Python library's concat internals — that's the completeness treadmill
    aimed at libraries. Report structural facts; user maps to their concat.

**Loader-completeness treadmill is avoided entirely:** the tool reports loader-agnostic
structural facts + drift forms; the user maps them to their specific loader's tolerance.
No per-product behavior matrix is maintained. Named loaders, if ever surfaced, are thin
presets over the two modes — never a modeled behavior set.

---

## 12. Drift forms (for `check` mode) — deterministic structural deltas vs baseline

The five industry-standard drift forms, and what's deterministically detectable:

| Form | Detectable structurally? | Notes |
|---|---|---|
| **Additive** (new column) | **Yes** | name mode: extra name; position mode: count up. Severity per extra-column policy (CLI flag: strict=ERROR/WARN, open=INFO). Most common + most silently dangerous. |
| **Missing/breaking** (dropped required column) | **Yes** | name mode: expected name absent (ERROR); position mode: count down (ERROR). |
| **Reorder** | **Yes** | name mode: non-event (names bind, order irrelevant) — correct result; position mode: changes type-at-position only if types differ. |
| **Type change** | **Yes** | via compatibility lattice: widening=WARN/INFO, breaking=ERROR. |
| **Rename** | **Candidate only** — never asserted | name mode: shows as (missing expected name) + (unexpected new name) at a position — surfaced as evidence ("position 7: expected `CustomerID`, found `AccountID`, same type"), verdict withheld; user's eye decides. NO content inference to "confirm" a rename (rejected — §13). In position mode with names-in-baseline, a positional name mismatch is the swap/rename signal → WARN. |
| **Semantic** (name+type same, meaning changed) | **No** — out of scope | The value-meaning hard limit. Disclosed, not chased. This is a data-quality concern. |

**Extra-column policy** (CLI flag on `check`): `strict` (any extra column is drift) vs
`open` (extras allowed; only declared columns checked). Maps to "my loader breaks on new
columns" vs "my loader tolerates them but I still want to know."

---

## 13. Rename / identity — explicit non-guessing policy

- The tool **reports observations, not inferences**. Column identity across a changed label
  is the **user's declaration** (via mode + baseline), never the tool's guess.
- **Cosmetic rename** (`CustomerID`→`customer_id`) → handled by canonicalization; not even a
  rename (same normalized name).
- **Genuine rename / same-type swap** → the tool exposes the *evidence* (expected vs found
  name at a position, same type) and **withholds the verdict**. In name mode it's a
  missing+added pair (ERROR: the name-bound load breaks). In position mode with names it's a
  positional name mismatch (WARN: the positional load tolerates it but may be mismapping).
- **Content-based rename/identity confirmation is REJECTED for verdicts.** Matching
  distributions/min-hashes to "confirm" two differently-named columns are the same is
  probabilistic (same-type columns confuse it; normal daily variation false-triggers it) and
  a confident-wrong identity claim is worse than silence. This crossed the no-guessing line
  and is out of drift/baseline detection entirely.
- **The value-meaning limit** (a header truthfully named but data underneath is wrong) fools
  BOTH modes and is undetectable structurally. It is the outer boundary of the deterministic
  enterprise. **Disclosed as a scope statement in every relevant output**, not chased:
  > "Conforms" = structurally matches the contract your load binds on. It does NOT mean the
  > data is correct. Whether values under a column mean what the column claims is not
  > structurally verifiable.

---

## 14. Output — the deliverable

**Primary data structure:** a list of files, each with a verdict. `file → verdict → findings`.
Family/variant/summary views are *groupings over* this list, not the other way around.

**Per-file verdict (the atomic unit):**
- `conforms` (golden) | `broken`
- If broken: each finding = `{ reason_code, severity, exact locus (column name / position),
  expected, found, explanation string }`.
- Specificity is mandatory and per-file, not per-family:
  - `vendor_2026_03_15.csv — position 4: expected decimal, found text (ERROR)`
  - `partner_feed_042.csv — missing column 'account_id' (ERROR)`
  - `export_final.csv — 32 columns, expected 31 (ERROR)`
  - `march.csv — position 2 named 'tax', baseline expects 'amount' (WARN: possible swap, positional load unaffected)`

**The golden/broken partition (the product):**
- **Golden set** (load these) and **broken set** (fix/skip these, each with exact reason).
  A positive "these 3,997 conform" assertion is as valuable as the broken list — it lets the
  DE carve out the known-good subset and proceed rather than blocking the whole bulk load on
  3 files.

**Every finding carries:** stable machine-readable `reason_code`, mode-derived `severity`,
human explanation, and confidence (= agreeing-signal count). Severity is a projection of
`(reason_code, alignment_mode)`.

**Accounting reconciliation** (§4.4) always surfaced.

**Formats:** `--json` (contract) + human text (renderer). Exportable inventory (CSV/HTML/JSON).

---

## 15. Commands (stateless; pure functions of their inputs)

- **`scan <path-expr> [flags]`** — describe a fileset's structure: families, variants,
  within-set outliers (the odd-one-out relative to the population — needs no baseline).
  Standalone-useful for archive/migration/vendor-onboarding one-shots.
- **`check <path-expr> --against <baseline> [--align name|position] [--extra strict|open]
  [flags]`** — diff each file's fingerprint against a user-supplied baseline; produce the
  golden/broken partition with exact per-file deviations and mode-appropriate severity.
  CI-native: exits non-zero on ERROR-grade drift (gates a merge / fails a pre-load step).

Both are pure functions. No state, no baseline store, no tracking. `check` = `scan`'s
fingerprint diffed against a parsed baseline on one side.

---

## 16. Cross-cutting invariants (apply everywhere; stated once)

- **Deterministic**: same input → same output. No randomness in verdicts (sampling is
  seeded/confirmed).
- **Explainable by construction**: every grouping/finding carries its reason + the signals
  that produced it. Grouping is done as signal-agreement, so the explanation is a byproduct.
- **Confidence = count of independent agreeing signals** (honest, not a probability).
- **Conservative over clever**: bias to showing differences, not asserting sameness. A
  false "broken" erodes trust as badly as a missed one.
- **Typed result object is the contract**; CLI `--json` emits it; text/GUI are renderers.
- **Path & baseline are data, not SQL** — never interpolated into query strings.
- **Read-only**; nothing written into scanned locations.
- **Every limit**: explicit default, overridable flag, stated finding when hit — never a
  silent truncation, never a crash on malformed input.
- **Full accounting**: files_seen = profiled + degraded + refused, always reconciled.
- **Value-meaning limit disclosed**, never chased.

---

## 17. Success metrics (what actually decides the tool)

1. **Precision of the golden/broken partition** — a false "broken" is as damaging as a
   missed broken file. Verdicts must be trustworthy.
2. **Speed at thousands-of-files scale** — must be a pre-flight step, not an overnight job.
   Sample-then-confirm keeps cost proportional to the number of anomalies, not files.

Feature breadth is secondary to these two. Get them right → indispensable. Get features
right but the partition is noisy or slow → a demo.

---

## 18. Moat & build notes

- **The moat is the accumulated edge-case heuristics** (lying extensions, encoding sniffing,
  header-that-isn't, ragged-row shift-detection, delimiter drift) + the **explanation quality**
  + the **ranked/named exactness of the golden/broken partition**. The engine, not the
  frontend, holds the moat. Spend early effort there.
- **The fixture corpus is a first-class asset** — a growing set of deliberately-cursed folders
  with known-correct expected outputs. It is simultaneously the regression suite AND the
  encoded form of hard-won file-format scars. Guard it; a competitor can't clone it from the
  README.
- **Library-engine-first is the correct build order** and the tight Claude-Code loop: cursed
  fixture folder in → typed result / JSON assertion out; no pixels to eyeball. Grind the engine
  + heuristics against fixtures until the output is right; the CLI and any visual renderer are
  thin layers over the already-correct library. (Package = library + CLI from one artifact, so
  "engine-first" and "package" are the same effort, not two.)

### Build order
0. **Package scaffold** — `pyproject.toml` (name, version, `duckdb` dependency, Python
   versions, console entry point `willitload`→CLI), `src/willitload/` layout, `tests/`. Get
   `pip install -e .` working locally and a trivial `willitload --version` before any logic.
   Practice a publish to **TestPyPI** early so the release path is proven, not a launch-day
   surprise. Pure-Python = one wheel; no build matrix.
1. Typed **finding schema** + `reason_code` taxonomy (the API — both the library's result
   objects and the `--json` shape — the assertion target, the frontend contract).
2. Type vocabulary + alias map (§6) — the same classes Tier-2 inference emits.
3. **Tier 0** acquisition/physical resolution + file accounting. Push globbing + reading into
   DuckDB from the start (§3.1 discipline); Python orchestrates only.
4. **Tier 1** header clustering + intra-file structural anomalies → per-file verdict partition.
5. Fixture corpus of cursed folders (regression suite + moat).
6. **Tier 2** type refinement + cross-file type-inference disagreement.
7. **`check`** mode: baseline parsers (flat file → prior-JSON → golden-file), the structural
   diff, severity projection, golden/broken partition. (DDL baseline deferred behind demand.)
8. Thin CLI renderer(s) — text first (over the same typed result), then optional visual
   bolt-on. Then publish to PyPI (or private index).

### Deferred / future (explicitly parked, not forgotten)
- **Tier 3** representation-uniformity (date-format/null-token/decimal-separator/encoding
  uniformity within a family). Structural, in-boundary, but parked to keep v1 crisp.
- **DDL baseline** parser (gate behind proven demand for the free baselines).
- **Stateful monitoring** (baseline store, time-series, alerting) — a *different animal*,
  deliberately out. Note: v1's stateless `check` already delivers the CI drift-gate without
  it, because the baseline is a user-supplied input, not stored state. Keep the stateless core
  architected so a future baseline-capture step is a thin inch, not a re-architecture.
- Additional baseline front-doors (dbt, Avro, JSON Schema) as thin parsers normalizing to the
  same fingerprint — only if demand proves out.
- **Rust core via PyO3/maturin** (§3.1 escape hatch) — only if profiling proves the Python
  *orchestration* layer (not DuckDB) is the bottleneck. Trades one universal pure-Python wheel
  for a per-OS/arch compiled-wheel matrix built in CI (`cibuildwheel`). Keeps `pip install` +
  Python API. Not a v1 concern.

---

## 19. Licensing & positioning — open, free, portfolio-first

**Decision: fully open-source and free. No paid tier, no open-core split, no monetization.**
This is a **portfolio / technical-authority project**, not a venture. That decision is
deliberate and it *simplifies* everything downstream:
- No licensing agonizing → permissive license (Apache-2.0 preferred over MIT for the patent
  grant; either is fine). Maximizes adoption and carries zero enterprise-legal friction, which
  matters because the whole distribution thesis is frictionless `pip install` into enterprise
  DE workflows.
- No conversion funnel, no crippled free tier, no support obligation to paying customers, no
  "different animal" to defer for business reasons. Build it the way that's most demonstrative,
  not most monetizable.
- The previously-"paid" layer (stateful monitor / dashboard / hosted service) simply stays
  **out of scope** as future work (§18 deferred), not behind a paywall. If ever built, it's
  just more open work or a separate concern — no license gymnastics needed.

**What "portfolio value" actually means here (drives §20):** a repo that *exists* is not a
portfolio piece; a repo that *demonstrates judgment* is. The rarer, higher-value signal is not
"can build" (everyone can `git push`) but "can decide what to build and what to **refuse**."
The scope discipline in this spec — the quality-line (§2), refusing to guess (§13), the honest
physics-limit disclosure (§13), the reverse-engineering of check-modes from load semantics (§5,
§11), the deliberate parking (§18) — IS the portfolio asset. Make that reasoning **visible**.

**Payoff is diffuse and slow** (a credential that helps land consulting/roles, not direct
revenue) — and that's the accepted, correct goal. Matching effort to that goal means: ship a
crisp bounded v1, foreground the judgment, don't half-build a business there's no appetite to
run.

---

## 20. Portfolio delivery — make the reasoning visible

The code proves you can build; the **reasoning proves seniority**. Extract maximum portfolio
value by treating the following as first-class deliverables, not afterthoughts:

- **The README is the portfolio piece**, not the source (most evaluators read the README, skim
  the code). It should:
  1. Lead with the **problem as a war story** (the 2am silently-broken bulk load; the one odd
     file in thousands).
  2. Show the **golden/broken output** on a real cursed fixture folder — the 30-second "wow"
     (names the 3 broken files out of 1,000 instantly).
  3. **Articulate the boundaries and why they exist** — "detects structural drift, NOT data
     quality, and here's the principled reason"; "positional mode cannot catch a same-type
     swap — that's physics, and here's how we disclose rather than fake it." Stating what the
     tool *refuses* to do, and why, signals more seniority than any feature list.
- **A 30-second runnable wow.** `pip install` → one-liner against a bundled cursed-fixture
  folder → instant named partition. The demo that gets shared is one the reader can *feel*
  immediately.
- **The fixture corpus does triple duty:** regression suite (§18) + moat (§18) + **demo/
  credibility material** ("look how many nasty cases it handles"). Build it deliberately; it's
  the encoded form of the file-format scars and the most convincing proof of depth.
- **Write it up on the technical-authority site (RamWise.dev)** as a **design-rationale /
  "why this tool refuses to do X" piece** — the decisions, especially the refusals. This spec
  is the raw material for that write-up. This is the content that compounds and does the actual
  promoting.
- **Ship the small honest core, not the sprawling half-built ambition.** A working, clearly-
  bounded v1 reads as maturity; a half-finished everything-tool reads as someone who couldn't
  finish. The parked roadmap (§18) *helps* — "v1 does X; here's the deliberately-deferred rest"
  shows intentional scoping, not running out of steam. **Shipping beats scope.**

**Success, for this project's actual goal:** a bounded, deterministic, honestly-limited tool
that works on real cursed folders, whose README and write-up make the *judgment* legible. That
is the promotion.
