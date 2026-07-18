"""
tests/test_revisions.py — Tests for the fixes introduced in the code review.
"""
from pathlib import Path
import pytest

from willitload.tier0.resolver import resolve, ResolverConfig
from willitload.tier0.encoding import detect_encoding
from willitload.core import scan, check
from willitload.baseline.flat import parse_flat_schema
from willitload.models import Bucket, ReasonCode, Severity


def test_baseline_file_excluded_from_check(tmp_path):
    """Verify that the baseline file itself is excluded from checks so it doesn't pollute scans."""
    # Write a flat schema file in the folder
    schema_path = tmp_path / "baseline.schema"
    schema_path.write_text("customer_id INT\nname TEXT\n", encoding="utf-8")

    # Write two CSV data files
    (tmp_path / "orders1.csv").write_text("customer_id,name\n1,Alice\n", encoding="utf-8")
    (tmp_path / "orders2.csv").write_text("customer_id,name\n2,Bob\n", encoding="utf-8")

    # Parse baseline
    baseline = parse_flat_schema(schema_path)

    # Run check targeting the entire folder (which normally expands to include baseline.schema)
    res = check(str(tmp_path), baseline)

    # Verify that only the 2 CSV files were seen/profiled, and baseline.schema was not included
    assert res.accounting.files_seen == 2
    assert res.accounting.profiled == 2
    assert res.accounting.catalogued == 0
    assert len(res.golden) == 2


def test_hidden_and_non_data_files(tmp_path):
    """Verify dotfiles/folders are ignored and non-data files (like .schema, .md, .py) are only catalogued."""
    # Create valid CSV
    (tmp_path / "data.csv").write_text("id,val\n1,x\n", encoding="utf-8")

    # Create hidden files and directories
    (tmp_path / ".hidden_file").write_text("secret content", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("git config here", encoding="utf-8")

    # Create non-data files
    (tmp_path / "readme.md").write_text("# Readme\nsome text here", encoding="utf-8")
    (tmp_path / "run.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "schema.schema").write_text("id,int\nval,text", encoding="utf-8")
    (tmp_path / "query.sql").write_text("SELECT * FROM table;", encoding="utf-8")

    # Scan the directory
    res = scan(str(tmp_path))

    # Dotfiles are completely ignored in expansion, while non-data files bucket as catalogued
    # We should have:
    # - 1 profiled file (data.csv)
    # - 4 catalogued files (readme.md, run.py, schema.schema, query.sql)
    # - 0 refused
    assert res.accounting.profiled == 1
    assert res.accounting.catalogued == 4
    assert res.accounting.files_seen == 5

    # Check that paths starting with '.' are not in the file verdicts at all
    paths_scanned = [v.path for v in res.file_verdicts]
    for p in paths_scanned:
        path_obj = Path(p)
        assert not any(part.startswith(".") for part in path_obj.parts if part != ".")


def test_sample_encoding_boundary(tmp_path):
    """Verify that a multibyte character straddling the 64KB sample cutoff is correctly decoded as UTF-8."""
    file_path = tmp_path / "boundary.csv"
    
    # 64KB is 65536 bytes. We write 65535 bytes of 'x', and then 'é' (which is b'\xc3\xa9' in UTF-8).
    # This places the first byte b'\xc3' at index 65535 (last byte of 64KB buffer)
    # and the second byte b'\xa9' at index 65536 (outside the buffer).
    prefix = b"x" * 65535
    char_straddle = b"\xc3\xa9"
    content = prefix + char_straddle

    with open(file_path, "wb") as f:
        f.write(content)

    # Sniff encoding of the file
    encoding, is_fallback = detect_encoding(file_path)

    # It must detect as UTF-8 (and NOT fall back to latin-1)
    assert encoding == "utf-8"
    assert is_fallback is False


def test_recursion_depth_ceiling(tmp_path):
    """Verify that the RECURSION_DEPTH_CEILING warning finding is raised if directories are nested too deep."""
    # Create deeply nested directories
    curr = tmp_path
    for i in range(12):  # 12 levels deep
        curr = curr / f"level{i}"
        curr.mkdir()
    
    # Write a file in the deepest level
    file_path = curr / "deep_file.csv"
    file_path.write_text("id,val\n1,x\n", encoding="utf-8")

    # Run resolver with max_recursion_depth limit set to 5
    config = ResolverConfig(max_recursion_depth=5)
    result = resolve(str(tmp_path), config)

    # Verify that a RECURSION_DEPTH_CEILING warning exists in set_findings
    warnings = [f for f in result.set_findings if f.reason_code == ReasonCode.RECURSION_DEPTH_CEILING]
    assert len(warnings) == 1
    assert warnings[0].severity == Severity.WARN
