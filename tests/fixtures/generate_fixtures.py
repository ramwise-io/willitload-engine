"""
Generate the starter fixture corpus for willitload tests.

Run: python tests/fixtures/generate_fixtures.py

Creates deliberately-cursed folders covering the main edge cases.
Each folder is a regression suite target; expected_output.json files
are added at Step 5 (formalization).
"""

import csv
import io
import json
import os
import random
import zipfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent
SEED = 42
rng = random.Random(SEED)

COLS_BASE = ["customer_id", "order_date", "amount", "status", "notes"]
TYPES_BASE = ["int", "date", "decimal", "text", "text"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_csv(
    path: Path,
    columns: list[str],
    rows: list[list],
    delimiter: str = ",",
    encoding: str = "utf-8",
    bom: bool = False,
    newline: str = "\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    writer.writerow(columns)
    writer.writerows(rows)
    content = buf.getvalue().replace("\r\n", newline).replace("\r", newline)
    mode = "wb" if bom else "w"
    if bom:
        path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    else:
        path.write_text(content, encoding=encoding)


def make_rows(n: int, seed: int = 0) -> list[list]:
    r = random.Random(seed)
    return [
        [
            r.randint(1000, 9999),
            f"2024-{r.randint(1,12):02d}-{r.randint(1,28):02d}",
            round(r.uniform(10.0, 9999.0), 2),
            r.choice(["PENDING", "SHIPPED", "DELIVERED"]),
            f"note_{i}",
        ]
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. column_drift — added/dropped/renamed columns across files
# ---------------------------------------------------------------------------

def gen_column_drift() -> None:
    base = FIXTURES_DIR / "column_drift"
    rows = make_rows(50)

    # 7 conforming files
    for i in range(1, 8):
        write_csv(base / f"orders_{i:03d}.csv", COLS_BASE, rows)

    # 1 file with an extra column (ADDITIVE)
    write_csv(
        base / "orders_extra.csv",
        COLS_BASE + ["region"],
        [r + ["US"] for r in rows],
    )

    # 1 file with a missing column (MISSING)
    write_csv(base / "orders_missing.csv", COLS_BASE[:4], [r[:4] for r in rows])

    # 1 file with a renamed column (RENAME candidate)
    write_csv(
        base / "orders_renamed.csv",
        ["customer_id", "order_date", "total_amount", "status", "notes"],
        rows,
    )

    # Baseline schema
    schema_lines = "\n".join(f"{name},{typ}" for name, typ in zip(COLS_BASE, TYPES_BASE))
    (base / "baseline.schema").write_text(schema_lines)

    print(f"  [column_drift] {len(list(base.glob('*.csv')))} CSVs + baseline.schema")


# ---------------------------------------------------------------------------
# 2. encoding_zoo — various encodings
# ---------------------------------------------------------------------------

def gen_encoding_zoo() -> None:
    base = FIXTURES_DIR / "encoding_zoo"
    base.mkdir(parents=True, exist_ok=True)
    rows = make_rows(10)
    content_lines = ["customer_id,order_date,amount,status,notes"]
    for r in rows:
        content_lines.append(",".join(str(v) for v in r))
    content = "\n".join(content_lines) + "\n"

    # UTF-8 (no BOM)
    (base / "utf8.csv").write_text(content, encoding="utf-8")

    # UTF-8 with BOM
    (base / "utf8_bom.csv").write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    # UTF-16 LE with BOM
    (base / "utf16le.csv").write_bytes(b"\xff\xfe" + content.encode("utf-16-le"))

    # Latin-1 (ENCODING_FALLBACK scenario: accented characters)
    latin_content = content.replace("note_", "nöte_")  # ö is not valid UTF-8 as a standalone byte
    (base / "latin1.csv").write_bytes(latin_content.encode("latin-1"))

    print(f"  [encoding_zoo] 4 files")


# ---------------------------------------------------------------------------
# 3. delimiter_drift — various delimiters
# ---------------------------------------------------------------------------

def gen_delimiter_drift() -> None:
    base = FIXTURES_DIR / "delimiter_drift"
    rows = make_rows(20)

    write_csv(base / "comma.csv", COLS_BASE, rows, delimiter=",")
    write_csv(base / "tab.csv", COLS_BASE, rows, delimiter="\t")
    write_csv(base / "pipe.csv", COLS_BASE, rows, delimiter="|")
    write_csv(base / "semicolon.csv", COLS_BASE, rows, delimiter=";")

    print(f"  [delimiter_drift] 4 files")


# ---------------------------------------------------------------------------
# 4. header_chaos — mixed header presence and "header-that-isn't"
# ---------------------------------------------------------------------------

def gen_header_chaos() -> None:
    base = FIXTURES_DIR / "header_chaos"
    rows = make_rows(20)

    # Normal file with header
    write_csv(base / "with_header.csv", COLS_BASE, rows)

    # File without header (headerless)
    base.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    (base / "no_header.csv").write_bytes(buf.getvalue().encode("utf-8"))

    # File where row 0 looks like data (all numbers) — "header that isn't"
    numeric_rows = [[i, i + 100, i * 1.5, i + 200, i + 300] for i in range(20)]
    buf2 = io.StringIO()
    writer2 = csv.writer(buf2)
    writer2.writerows(numeric_rows)
    (base / "numeric_header.csv").write_bytes(buf2.getvalue().encode("utf-8"))

    print(f"  [header_chaos] 3 files")


# ---------------------------------------------------------------------------
# 5. ragged — ragged rows, truncated, trailing summary
# ---------------------------------------------------------------------------

def gen_ragged() -> None:
    base = FIXTURES_DIR / "ragged"
    base.mkdir(parents=True, exist_ok=True)

    # Ragged: one row has extra delimiter (unescaped comma in notes field)
    lines = [",".join(COLS_BASE)]
    for i, r in enumerate(make_rows(20)):
        if i == 10:
            # Inject ragged row: extra comma in notes
            lines.append(f"{r[0]},{r[1]},{r[2]},{r[3]},note with, comma")
        else:
            lines.append(",".join(str(v) for v in r))
    (base / "ragged_rows.csv").write_text("\n".join(lines) + "\n")

    # Truncated: file cut mid-row (no trailing newline, partial last row)
    normal_content = "\n".join([",".join(COLS_BASE)] + [",".join(str(v) for v in r) for r in make_rows(20)])
    truncated = normal_content[:len(normal_content) - 15]  # cut mid-last-row
    (base / "truncated.csv").write_bytes(truncated.encode("utf-8"))  # no trailing newline

    # Trailing summary row
    lines2 = [",".join(COLS_BASE)]
    data_rows = make_rows(20)
    for r in data_rows:
        lines2.append(",".join(str(v) for v in r))
    total_amount = sum(r[2] for r in data_rows)
    lines2.append(f"TOTAL,{total_amount:.2f}")  # 2 columns (breaks the 5-column pattern)
    (base / "trailing_summary.csv").write_text("\n".join(lines2) + "\n")

    print(f"  [ragged] 3 files")


# ---------------------------------------------------------------------------
# 6. type_drift — same header, type drift across files
# ---------------------------------------------------------------------------

def gen_type_drift() -> None:
    base = FIXTURES_DIR / "type_drift"
    rows = make_rows(20)

    # Conforming files: customer_id is int
    for i in range(1, 6):
        write_csv(base / f"orders_{i:03d}.csv", COLS_BASE, rows)

    # Drifted: customer_id stored as text (e.g. "CUST-1234")
    drifted_rows = [[f"CUST-{r[0]}"] + r[1:] for r in rows]
    write_csv(base / "orders_id_as_text.csv", COLS_BASE, drifted_rows)

    # Drifted: amount stored as text (e.g. "$99.99")
    drifted_rows2 = [r[:2] + [f"${r[2]}"] + r[3:] for r in rows]
    write_csv(base / "orders_amount_as_text.csv", COLS_BASE, drifted_rows2)

    print(f"  [type_drift] {len(list(base.glob('*.csv')))} files")


# ---------------------------------------------------------------------------
# 7. archive_set — ZIPs containing CSVs
# ---------------------------------------------------------------------------

def gen_archive_set() -> None:
    base = FIXTURES_DIR / "archive_set"
    base.mkdir(parents=True, exist_ok=True)
    rows = make_rows(10)

    # Create CSV content
    buf = io.StringIO()
    csv.writer(buf).writerow(COLS_BASE)
    for r in rows:
        csv.writer(buf).writerow(r)
    csv_content = buf.getvalue().encode("utf-8")

    # Normal ZIP with one CSV
    with zipfile.ZipFile(base / "archive_normal.zip", "w") as zf:
        zf.writestr("orders.csv", csv_content)

    # ZIP with multiple CSVs
    with zipfile.ZipFile(base / "archive_multi.zip", "w") as zf:
        zf.writestr("orders_1.csv", csv_content)
        zf.writestr("orders_2.csv", csv_content)

    print(f"  [archive_set] 2 ZIPs")


# ---------------------------------------------------------------------------
# 8. extension_lying — files with wrong extensions
# ---------------------------------------------------------------------------

def gen_extension_lying() -> None:
    base = FIXTURES_DIR / "extension_lying"
    base.mkdir(parents=True, exist_ok=True)
    rows = make_rows(5)

    # A JSON file named .csv
    json_data = [
        {"customer_id": r[0], "order_date": str(r[1]), "amount": r[2]}
        for r in rows
    ]
    (base / "looks_like_csv.csv").write_text(json.dumps(json_data))

    # A proper CSV for comparison
    write_csv(base / "real.csv", COLS_BASE[:3], [r[:3] for r in rows])

    print(f"  [extension_lying] 2 files")


# ---------------------------------------------------------------------------
# 9. clean_conforming — small clean set for basic smoke tests
# ---------------------------------------------------------------------------

def gen_clean_conforming() -> None:
    base = FIXTURES_DIR / "clean_conforming"
    rows = make_rows(50)
    for i in range(1, 21):
        write_csv(base / f"orders_{i:03d}.csv", COLS_BASE, rows)

    schema_lines = "\n".join(f"{name},{typ}" for name, typ in zip(COLS_BASE, TYPES_BASE))
    (base / "baseline.schema").write_text(schema_lines)
    print(f"  [clean_conforming] 20 CSVs + baseline.schema")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Generating fixture corpus...")
    gen_column_drift()
    gen_encoding_zoo()
    gen_delimiter_drift()
    gen_header_chaos()
    gen_ragged()
    gen_type_drift()
    gen_archive_set()
    gen_extension_lying()
    gen_clean_conforming()
    print("\nDone. Fixture corpus generated.")
