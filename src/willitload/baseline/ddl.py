"""
willitload.baseline.ddl — DDL (CREATE TABLE) baseline front-door.

Parses names, types, and column order from simple DDL CREATE TABLE statements.
"""
from __future__ import annotations

import re
from pathlib import Path

from willitload.baseline.fingerprint import BaselineFingerprint, BaselineColumn
from willitload.types import normalize_type


def parse_ddl_schema(path_or_text: str | Path) -> BaselineFingerprint:
    """
    Parse a CREATE TABLE DDL schema file or raw string and return a BaselineFingerprint.
    """
    path_or_text_str = str(path_or_text)
    source_desc = f"DDL schema: {path_or_text_str}"

    content = ""
    try:
        if Path(path_or_text_str).exists():
            with open(path_or_text_str, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                source_desc = f"DDL schema file: {path_or_text_str}"
        else:
            content = path_or_text_str
    except Exception:
        content = path_or_text_str

    # 1. Clean SQL comments and collapse whitespace
    # Block comments
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    # Line comments
    content = re.sub(r"--.*", "", content)
    content = " ".join(content.split())

    # 2. Extract column definitions block between outermost parentheses of CREATE TABLE
    # Matches "CREATE TABLE tablename ( ... )"
    match = re.search(
        r"CREATE\s+(?:TEMP\s+|TEMPORARY\s+)?TABLE\s+(?:\w+\.)?(?:\w+|\[\w+\]|`\w+`)\s*\((.*)\)",
        content,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("Could not find CREATE TABLE statement or column definition block in DDL input")

    cols_block = match.group(1).strip()

    # Split by comma at depth 0 to avoid splitting type args like DECIMAL(10,2)
    raw_defs = []
    current = []
    depth = 0
    for char in cols_block:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            raw_defs.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        raw_defs.append("".join(current).strip())

    columns: list[BaselineColumn] = []
    for d in raw_defs:
        if not d:
            continue
        upper_d = d.upper()
        # Skip constraint lines (primary keys, table constraints etc.)
        if any(
            upper_d.startswith(prefix)
            for prefix in ("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT", "UNIQUE", "CHECK")
        ):
            continue

        tokens = d.split()
        if len(tokens) < 2:
            continue

        col_name = tokens[0].strip('"\'`[]')
        col_type_raw = tokens[1]

        # Extract base type name (e.g. "VARCHAR" from "VARCHAR(100)")
        type_match = re.match(r"^([a-zA-Z0-9_]+)", col_type_raw)
        if type_match:
            type_name = type_match.group(1).lower()
        else:
            type_name = col_type_raw.lower()

        inferred_class = normalize_type(type_name)

        # Canonicalize name
        from willitload.tier1.canonicalize import canonicalize_name
        trace = canonicalize_name(col_name)
        norm_name = trace.normalized

        columns.append(
            BaselineColumn(
                name=norm_name,
                raw_name=col_name,
                type_class=inferred_class,
                position=len(columns),
            )
        )

    if not columns:
        raise ValueError("No valid columns parsed from DDL schema")

    return BaselineFingerprint(
        columns=columns,
        source_description=source_desc,
    )
