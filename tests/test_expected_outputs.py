"""
tests/test_expected_outputs.py — Integration tests verifying all fixture folders match their expected output.
"""
import json
import pathlib
import pytest

from willitload.core import scan

WORKSPACE_ROOT = pathlib.Path(__file__).parent.parent.resolve()
FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

def normalize_verdict(val, rel_to):
    if "path" in val:
        val["path"] = str(pathlib.Path(val["path"]).relative_to(rel_to)).replace("\\", "/")
    return val

def normalize_scan_result(data, rel_to):
    data["elapsed_ms"] = 0.0
    data["path_expression"] = str(pathlib.Path(data["path_expression"]).relative_to(rel_to)).replace("\\", "/")
    if "file_verdicts" in data:
        data["file_verdicts"] = [normalize_verdict(v, rel_to) for v in data["file_verdicts"]]
        data["file_verdicts"].sort(key=lambda x: x.get("path", ""))
    return data

@pytest.mark.parametrize(
    "folder_name",
    [
        "archive_set",
        "clean_conforming",
        "column_drift",
        "delimiter_drift",
        "encoding_zoo",
        "extension_lying",
        "header_chaos",
        "ragged",
        "type_drift",
    ],
)
def test_fixture_output_matches_snapshot(folder_name):
    folder = FIXTURES_DIR / folder_name
    expected_path = WORKSPACE_ROOT / "tests" / "expected_outputs" / f"{folder_name}.json"
    assert expected_path.exists(), f"Missing expected output for {folder_name}"
    
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = json.load(f)
        
    res = scan(str(folder))
    res_dict = json.loads(res.to_json())
    actual = normalize_scan_result(res_dict, WORKSPACE_ROOT)
    
    # Assert keys match exactly
    assert actual == expected
