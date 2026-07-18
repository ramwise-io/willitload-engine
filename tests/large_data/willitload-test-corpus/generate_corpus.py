#!/usr/bin/env python3
"""
generate_corpus.py — build a multi-kind synthetic test corpus for willitload.

Parent directories produced under the output root:
  01_breadth/      — coverage of specific edge cases, each folder = one phenomenon
  02_demo/         — realistic story-driven "vendor feed gone wrong" for README/blog
  03_scale/        — thousands of files, mostly clean + a few needles (perf path)
  04_blind/        — adversarial folders; problems seeded WITHOUT inline hints
  ANSWER_KEY.md    — hidden truth for 04_blind (score willitload against this)

Everything is deterministic (fixed seed) so the corpus regenerates identically.
"""
from __future__ import annotations
import gzip
import io
import os
import random
import struct
import zipfile
from pathlib import Path

SEED = 20260718
random.seed(SEED)

ROOT = Path("wilcorpus")

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def w(path: Path, content: str, encoding: str = "utf-8", newline: str = "\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.replace("\n", newline).encode(encoding)
    path.write_bytes(data)

def wbytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

def orders_rows(n, start=1000, cols=("order_id","customer_id","sku","quantity","revenue")):
    lines = [",".join(cols)]
    for i in range(n):
        lines.append(f"{start+i},{random.randint(1,999)},SKU-{random.randint(1,50)},"
                     f"{random.randint(1,9)},{random.randint(1,999)}.{random.randint(0,99):02d}")
    return "\n".join(lines) + "\n"

# ===========================================================================
# 01_breadth — one phenomenon per folder, each with a README describing intent
# ===========================================================================
def build_breadth():
    base = ROOT / "01_breadth"

    # --- encoding_boundary: valid UTF-8 with a multibyte char straddling 64KB ---
    d = base / "encoding_boundary"
    pad = "a" * (64 * 1024 - 1)
    w(d / "straddle_64k.csv", "note\n" + pad + "\u20ac" + "tail\n")      # € across 64KB
    w(d / "straddle_1mb.csv", "note\n" + "b" * (1024*1024 - 1) + "\u00e9" + "x\n")  # é across 1MB
    w(d / "clean_utf8.csv", "city,name\nMünchen,José\nTōkyō,Zoë\n")
    w(d / "README.txt", "All three files are VALID UTF-8. The straddle files place a "
                        "multibyte char exactly on a buffer boundary. willitload must NOT "
                        "flag these as ENCODING_FALLBACK or DECODE_ERROR (false positive).\n")

    # --- encoding_zoo_plus: genuine encoding variety incl. genuinely-broken bytes ---
    d = base / "encoding_zoo"
    w(d / "utf8.csv", "id,city\n1,Zürich\n2,Kraków\n")
    w(d / "utf8_bom.csv", "id,city\n1,Oslo\n", encoding="utf-8-sig")
    w(d / "utf16le.csv", "id,city\n1,Berlin\n2,Madrid\n", encoding="utf-16-le")
    w(d / "latin1.csv", "id,city\n1,Montréal\n", encoding="latin-1")
    wbytes(d / "broken_bytes.csv", b"id,city\n1,caf" + b"\xff\xfe" + b"e\n")  # invalid utf-8
    w(d / "README.txt", "utf8/utf8_bom/utf16le/latin1 are all decodable. broken_bytes.csv "
                        "has genuinely invalid byte sequences and should be flagged.\n")

    # --- delimiter_variety ---
    d = base / "delimiter_variety"
    w(d / "comma.csv", "a,b,c\n1,2,3\n")
    w(d / "tab.csv", "a\tb\tc\n1\t2\t3\n")
    w(d / "pipe.csv", "a|b|c\n1|2|3\n")
    w(d / "semicolon.csv", "a;b;c\n1;2;3\n")
    w(d / "README.txt", "Same logical schema, four delimiters. Tests delimiter inference.\n")

    # --- header_ambiguity ---
    d = base / "header_ambiguity"
    w(d / "has_header.csv", "order_id,amount\n1001,9.99\n1002,8.50\n")
    w(d / "no_header.csv", "1001,9.99\n1002,8.50\n1003,7.25\n")       # data-only, no header
    w(d / "numeric_header.csv", "1,2\n1001,9.99\n")                   # header-that-isnt
    w(d / "all_text_header_all_text_data.csv", "name,city\nfoo,bar\nbaz,qux\n")
    w(d / "README.txt", "Mixed header presence. no_header has no header row; numeric_header's "
                        "first row looks like data. Header-presence detection is under test.\n")

    # --- type_edge: values that stress the type-inference ladder ---
    d = base / "type_edge"
    w(d / "leading_zeros.csv", "zip,val\n01234,5\n00567,6\n")          # should stay text, not int
    w(d / "id_with_letter.csv", "id,v\n12345678,1\n1234567D,2\n")      # trailing letter forces text
    w(d / "mixed_int_text.csv", "qty,v\n5,a\nmany,b\n")                # 'many' forces text
    w(d / "bool_like.csv", "flag,v\ntrue,1\nfalse,2\n")
    w(d / "dates_iso.csv", "d,v\n2026-01-05,1\n2026-01-06,2\n")
    w(d / "README.txt", "Type-inference edge cases: leading zeros, IDs with letters, "
                        "int/text mix, booleans, ISO dates.\n")

    # --- ragged_and_truncated ---
    d = base / "ragged_and_truncated"
    w(d / "ragged.csv", "a,b,c\n1,2,3\n4,5,6,7\n8,9,10\n")             # row 2 has extra field
    w(d / "trailing_total.csv", "region,amount\nEast,100\nWest,200\nTOTAL,300\n")
    w(d / "truncated.csv", "a,b,c\n1,2,3\n4,5")                        # last row cut off, no newline
    w(d / "unescaped_comma.csv", 'name,note,amt\n"Acme, Inc",ok,10\nBeta,two, words,20\n')
    w(d / "README.txt", "Structural anomalies within single files: ragged rows, a trailing "
                        "TOTAL row, a truncated final row, and an unescaped comma shifting fields.\n")

    # --- format_zoo: extension-lying + real formats ---
    d = base / "format_zoo"
    w(d / "real.csv", "a,b\n1,2\n")
    w(d / "actually_json.csv", '{"a":1,"b":2}\n{"a":3,"b":4}\n')       # .csv but JSONL
    w(d / "data.jsonl", '{"id":1,"v":"x"}\n{"id":2,"v":"y"}\n')
    w(d / "data.json", '[{"id":1,"v":"x"},{"id":2,"v":"y"}]\n')
    # a tiny real parquet via duckdb if available, else skip gracefully
    try:
        import duckdb
        con = duckdb.connect()
        con.execute(f"COPY (SELECT 1 AS id, 'x' AS v UNION ALL SELECT 2,'y') "
                    f"TO '{(d/'data.parquet').as_posix()}' (FORMAT parquet)")
        con.close()
    except Exception:
        pass
    # sqlite file
    try:
        import sqlite3
        con = sqlite3.connect(d / "data.sqlite")
        con.execute("CREATE TABLE t(id INTEGER, v TEXT)")
        con.executemany("INSERT INTO t VALUES(?,?)", [(1,"x"),(2,"y")])
        con.commit(); con.close()
    except Exception:
        pass
    w(d / "README.txt", "Mixed real formats plus an extension-liar: actually_json.csv is JSONL "
                        "wearing a .csv extension. Format-by-content (not extension) is under test.\n")

    # --- archive_health: gzip + zip, healthy and corrupt ---
    d = base / "archive_health"
    healthy = orders_rows(30)
    wbytes(d / "healthy.csv.gz", gzip.compress(healthy.encode()))
    # corrupt gzip: valid header then truncated body
    good_gz = gzip.compress(healthy.encode())
    wbytes(d / "corrupt_truncated.csv.gz", good_gz[: len(good_gz)//2])
    # gzip whose decompressed content has bad bytes
    bad_inner = b"id,city\n1,caf" + b"\xff\xfe" + b"e\n"
    wbytes(d / "gz_decode_error.csv.gz", gzip.compress(bad_inner))
    # a normal zip of csvs
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.csv", "x,y\n1,2\n"); z.writestr("b.csv", "x,y\n3,4\n")
    wbytes(d / "normal.zip", buf.getvalue())
    # nested zip (zip within zip)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("deep.csv", "p,q\n1,2\n")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("inner.zip", inner.getvalue())
    wbytes(d / "nested.zip", outer.getvalue())
    w(d / "README.txt", "healthy.csv.gz decompresses+decodes clean. corrupt_truncated.csv.gz is "
                        "a cut-off gzip (CORRUPT_ARCHIVE). gz_decode_error.csv.gz decompresses but "
                        "has bad bytes (DECODE_ERROR). normal.zip is fine; nested.zip holds a zip.\n")

    # --- baseline_beside_data: the footgun — baseline lives in the scanned folder ---
    d = base / "baseline_beside_data"
    for i in range(1, 6):
        w(d / f"orders_{i:03d}.csv", orders_rows(10, start=1000+i*10))
    w(d / "baseline.schema", "order_id,int\ncustomer_id,int\nsku,text\nquantity,int\nrevenue,decimal\n")
    w(d / "README_notes.md", "# vendor notes\nThese are the January order feeds.\n")
    w(d / ".hidden_meta", "internal bookkeeping\n")
    w(d / "README.txt", "5 real data files + baseline.schema + a README + a dotfile. willitload "
                        "must NOT profile baseline.schema/README/dotfile AS DATA or form phantom "
                        "families. Tests the file-selection contract.\n")

# ===========================================================================
# 02_demo — a believable vendor feed with a realistic mix of problems
# ===========================================================================
def build_demo():
    d = ROOT / "02_demo" / "acme_daily_orders"
    cols = "order_id,customer_id,sku,quantity,unit_price,order_date"
    def good(day, start):
        rows = [cols]
        for i in range(12):
            rows.append(f"{start+i},{random.randint(100,999)},SKU-{random.randint(1,200)},"
                        f"{random.randint(1,6)},{random.randint(5,99)}.{random.randint(0,99):02d},"
                        f"2026-02-{day:02d}")
        return "\n".join(rows) + "\n"
    # a run of clean daily files
    for day in range(1, 11):
        w(d / f"orders_2026-02-{day:02d}.csv", good(day, 5000 + day*100))
    # day 11: vendor silently ADDED a column (discount) in the middle
    rows = ["order_id,customer_id,sku,discount,quantity,unit_price,order_date"]
    for i in range(12):
        rows.append(f"{5000+1100+i},{random.randint(100,999)},SKU-{random.randint(1,200)},"
                    f"{random.randint(0,20)},{random.randint(1,6)},"
                    f"{random.randint(5,99)}.{random.randint(0,99):02d},2026-02-11")
    w(d / "orders_2026-02-11.csv", "\n".join(rows) + "\n")
    # day 12: renamed customer_id -> account_id
    rows = ["order_id,account_id,sku,quantity,unit_price,order_date"]
    for i in range(12):
        rows.append(f"{5000+1200+i},{random.randint(100,999)},SKU-{random.randint(1,200)},"
                    f"{random.randint(1,6)},{random.randint(5,99)}.{random.randint(0,99):02d},2026-02-12")
    w(d / "orders_2026-02-12.csv", "\n".join(rows) + "\n")
    # day 13: unit_price arrived with a currency symbol -> forces text
    rows = [cols]
    for i in range(12):
        rows.append(f"{5000+1300+i},{random.randint(100,999)},SKU-{random.randint(1,200)},"
                    f"{random.randint(1,6)},${random.randint(5,99)}.{random.randint(0,99):02d},2026-02-13")
    w(d / "orders_2026-02-13.csv", "\n".join(rows) + "\n")
    # day 14: truncated export (died halfway, missing rows + no final newline)
    rows = [cols]
    for i in range(3):
        rows.append(f"{5000+1400+i},{random.randint(100,999)},SKU-1,1,9.99,2026-02-14")
    w(d / "orders_2026-02-14.csv", "\n".join(rows))  # no trailing newline, few rows
    # the baseline, placed OUTSIDE the data folder (correct usage)
    w(ROOT / "02_demo" / "expected.schema",
      "order_id,int\ncustomer_id,int\nsku,text\nquantity,int\nunit_price,decimal\norder_date,date\n")
    w(ROOT / "02_demo" / "README.txt",
      "A realistic Acme vendor feed: 10 clean daily files, then day 11 adds a column, "
      "day 12 renames one, day 13 corrupts unit_price with a '$', day 14 is truncated. "
      "Run: willitload check 02_demo/acme_daily_orders --against 02_demo/expected.schema --align name\n")

# ===========================================================================
# 03_scale — many files, mostly clean, a few needles
# ===========================================================================
def build_scale():
    d = ROOT / "03_scale" / "wide_feed"
    N = 1200
    cols = "id,customer,sku,qty,price,ts"
    needles = {  # index -> what's wrong
        137: "extra_col",
        512: "missing_col",
        888: "type_drift",
        1050: "renamed_col",
    }
    for i in range(N):
        if i == 137:
            content = "id,customer,sku,qty,price,ts,coupon\n" + \
                      "\n".join(f"{i}_{j},C{j},S{j},1,9.99,2026-01-01,X" for j in range(5)) + "\n"
        elif i == 512:
            content = "id,customer,sku,qty,ts\n" + \
                      "\n".join(f"{i}_{j},C{j},S{j},1,2026-01-01" for j in range(5)) + "\n"
        elif i == 888:
            content = "id,customer,sku,qty,price,ts\n" + \
                      "\n".join(f"{i}_{j},C{j},S{j},lots,9.99,2026-01-01" for j in range(5)) + "\n"
        elif i == 1050:
            content = "id,client,sku,qty,price,ts\n" + \
                      "\n".join(f"{i}_{j},C{j},S{j},1,9.99,2026-01-01" for j in range(5)) + "\n"
        else:
            content = cols + "\n" + \
                      "\n".join(f"{i}_{j},C{j},S{j},1,9.99,2026-01-01" for j in range(5)) + "\n"
        w(d / f"part_{i:05d}.csv", content)
    w(ROOT / "03_scale" / "baseline.schema",
      "id,text\ncustomer,text\nsku,text\nqty,int\nprice,decimal\nts,date\n")
    w(ROOT / "03_scale" / "README.txt",
      f"{N} files, {N-len(needles)} clean + {len(needles)} needles "
      f"(extra col @137, missing col @512, type drift @888, renamed col @1050). "
      f"Perf + needle-in-haystack test. Baseline sits outside the data folder.\n")

# ===========================================================================
# 04_blind — adversarial; NO inline hints. Truth kept only in ANSWER_KEY.md
# ===========================================================================
def build_blind():
    base = ROOT / "04_blind"
    key = ["# ANSWER KEY — 04_blind", "",
           "Kept separate on purpose. Run willitload against each folder, record its",
           "verdicts, THEN compare here. Score precision (false positives) and recall",
           "(missed problems).", ""]

    # case_alpha: looks messy, is actually all fine (tests false-positive rate)
    d = base / "case_alpha"
    for i in range(1, 9):
        # varied delimiters + encodings but all VALID and same logical schema
        w(d / f"f{i:02d}.csv", "id,name,amt\n1,Zoë,9.99\n2,José,8.50\n")
    w(d / "f09.csv", "id,name,amt\n3,Müller,7.25\n", encoding="utf-8")
    key += ["## case_alpha", "TRUTH: all 9 files conform. Same schema, valid UTF-8 throughout.",
            "Trap: accented names may tempt an encoding false-positive. Expect: ALL GOLDEN.", ""]

    # case_beta: one file has a middle-inserted column (the classic silent killer)
    d = base / "case_beta"
    for i in range(1, 7):
        w(d / f"day{i}.csv", "order_id,sku,qty,total\n"
                             f"{100+i},S{i},2,19.98\n{200+i},S{i},1,9.99\n")
    w(d / "day7.csv", "order_id,sku,region,qty,total\n"   # region inserted in middle
                      "107,S7,EAST,2,19.98\n")
    key += ["## case_beta", "TRUTH: day7.csv has an EXTRA column 'region' inserted mid-row.",
            "Others (day1-6) conform. Expect: day7 BROKEN (extra col), rest GOLDEN.", ""]

    # case_gamma: same-type swap (only detectable via header names, WARN in position mode)
    d = base / "case_gamma"
    w(d / "a.csv", "id,amount,tax\n1,100.00,8.00\n")
    w(d / "b.csv", "id,tax,amount\n2,8.50,120.00\n")   # amount<->tax swapped, both decimal
    w(d / "c.csv", "id,amount,tax\n3,90.00,7.20\n")
    key += ["## case_gamma", "TRUTH: b.csv swaps amount<->tax (same type, decimal).",
            "In NAME mode: a/b/c all conform (names bind, swap is a non-event).",
            "In POSITION mode vs a name-bearing baseline: b.csv should WARN (possible swap).",
            "This is the physics-limit case — no ERROR is possible, only a WARN via names.", ""]

    # case_delta: a genuinely corrupt gzip hidden among healthy ones
    d = base / "case_delta"
    good = orders_rows(20)
    wbytes(d / "feed_01.csv.gz", gzip.compress(good.encode()))
    wbytes(d / "feed_02.csv.gz", gzip.compress(orders_rows(15).encode()))
    g = gzip.compress(good.encode())
    wbytes(d / "feed_03.csv.gz", g[:len(g)//3])  # truncated -> corrupt
    key += ["## case_delta", "TRUTH: feed_03.csv.gz is a truncated/corrupt gzip.",
            "feed_01/02 are healthy. Expect: feed_03 BROKEN (CORRUPT_ARCHIVE), rest GOLDEN.", ""]

    # case_epsilon: type drift across files (int id becomes text in one)
    d = base / "case_epsilon"
    for i in range(1, 6):
        w(d / f"p{i}.csv", "id,qty,price\n"
                           f"{1000+i},3,9.99\n{2000+i},1,4.50\n")
    w(d / "p6.csv", "id,qty,price\nABC-99,3,9.99\n")   # id now text
    key += ["## case_epsilon", "TRUTH: p6.csv has a text id ('ABC-99') where others are int.",
            "Expect (vs int-id baseline): p6 BROKEN (type mismatch on id), rest GOLDEN.", ""]

    # case_zeta: everything drifted the SAME way (whole-folder drift; only a baseline catches it)
    d = base / "case_zeta"
    for i in range(1, 8):
        # every file is missing 'revenue' that the baseline expects
        w(d / f"o{i}.csv", "order_id,customer_id,sku,quantity\n"
                           f"{300+i},{i},S{i},2\n")
    key += ["## case_zeta", "TRUTH: ALL 7 files are internally consistent with each other but",
            "ALL are missing 'revenue' vs the expected baseline. Outlier detection alone",
            "(no baseline) would call these clean; only a baseline check catches the",
            "whole-folder drift. Expect (vs baseline w/ revenue): ALL 7 BROKEN (missing revenue).", ""]

    # a baseline provided for the blind set (outside the folders)
    w(base / "baseline_orders.schema",
      "order_id,int\ncustomer_id,int\nsku,text\nquantity,int\nrevenue,decimal\n")
    w(base / "baseline_idqtyprice.schema", "id,int\nqty,int\nprice,decimal\n")

    (ROOT / "04_blind" / "ANSWER_KEY.md").write_text("\n".join(key), encoding="utf-8")

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    build_breadth()
    build_demo()
    build_scale()
    build_blind()
    # count
    total = sum(1 for _ in ROOT.rglob("*") if _.is_file())
    print(f"corpus built under {ROOT}/  —  {total} files")
    for p in sorted(ROOT.iterdir()):
        if p.is_dir():
            n = sum(1 for _ in p.rglob("*") if _.is_file())
            print(f"  {p.name}/  ({n} files)")
