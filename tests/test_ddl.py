"""
tests/test_ddl.py — Unit and integration tests for DDL (CREATE TABLE) baseline schema parser.
"""
from pathlib import Path

import pytest

from willitload.baseline.ddl import parse_ddl_schema
from willitload.types import TypeClass


def test_standard_ddl_parsing():
    ddl = """
    CREATE TABLE customers (
        customer_id INT PRIMARY KEY,
        first_name VARCHAR(50) NOT NULL,
        signup_date DATE,
        balance DECIMAL(12, 2) DEFAULT 0.00
    )
    """
    fp = parse_ddl_schema(ddl)

    assert fp.column_count == 4
    assert fp.ordered_names == ["customer_id", "first_name", "signup_date", "balance"]
    assert fp.ordered_types == [
        TypeClass.INT,
        TypeClass.TEXT,
        TypeClass.DATE,
        TypeClass.DECIMAL,
    ]


def test_ddl_with_comments_and_constraints():
    ddl = """
    -- This is a comment
    CREATE TABLE orders (
        order_id BIGINT,
        customer_id INTEGER,
        order_date TIMESTAMP,
        is_active BOOLEAN,
        PRIMARY KEY (order_id),
        CONSTRAINT fk_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );
    """
    fp = parse_ddl_schema(ddl)

    assert fp.column_count == 4
    assert fp.ordered_names == ["order_id", "customer_id", "order_date", "is_active"]
    assert fp.ordered_types == [
        TypeClass.INT,
        TypeClass.INT,
        TypeClass.TIMESTAMP,
        TypeClass.BOOL,
    ]


def test_ddl_file_parsing(tmp_path):
    ddl_file = tmp_path / "schema.sql"
    ddl_file.write_text(
        "CREATE TABLE test (id INT, note TEXT);", encoding="utf-8"
    )

    fp = parse_ddl_schema(ddl_file)
    assert fp.column_count == 2
    assert fp.ordered_names == ["id", "note"]
    assert fp.ordered_types == [TypeClass.INT, TypeClass.TEXT]


def test_invalid_ddl_raises():
    with pytest.raises(ValueError, match="Could not find CREATE TABLE"):
        parse_ddl_schema("SELECT * FROM table")
