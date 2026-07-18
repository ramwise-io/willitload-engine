"""
tests/test_excel.py — Unit and integration tests for Excel (.xlsx) file profiling.
"""
from pathlib import Path

import pytest

from willitload.tier0.physical import PhysicalFile
from willitload.tier0.parsers import profile_excel
from willitload.types import TypeClass


def test_excel_profiling(tmp_path):
    import openpyxl

    xlsx_path = tmp_path / "test.xlsx"

    # Create synthetic workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "orders"

    # Write header and data rows
    ws.append(["customer_id", "order_date", "amount", "status", "notes"])
    ws.append([7311, "2024-07-02", 2596.32, "DELIVERED", "note_0"])
    ws.append([8961, "2024-07-26", 8299.40, "SHIPPED", "note_1"])
    wb.save(xlsx_path)

    # Profile
    pf = PhysicalFile(path=xlsx_path, size_bytes=xlsx_path.stat().st_size)
    profile_excel(xlsx_path, pf)

    assert pf.bucket.value == "profiled"
    assert pf.column_count == 5
    assert pf.raw_column_names == ["customer_id", "order_date", "amount", "status", "notes"]
    assert pf.column_types["customer_id"] == TypeClass.INT
    assert pf.column_types["amount"] == TypeClass.DECIMAL
    assert pf.column_types["status"] == TypeClass.TEXT
