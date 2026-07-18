"""
Tests for willitload.types — TypeClass, alias normalization, compatibility lattice.
"""

import pytest
from willitload.types import (
    TypeClass,
    Compatibility,
    normalize_type,
    check_compatibility,
)


class TestNormalizeType:
    def test_canonical_names_pass_through(self):
        assert normalize_type("int") == TypeClass.INT
        assert normalize_type("decimal") == TypeClass.DECIMAL
        assert normalize_type("text") == TypeClass.TEXT
        assert normalize_type("bool") == TypeClass.BOOL
        assert normalize_type("date") == TypeClass.DATE
        assert normalize_type("timestamp") == TypeClass.TIMESTAMP

    def test_aliases_normalize_correctly(self):
        assert normalize_type("integer") == TypeClass.INT
        assert normalize_type("bigint") == TypeClass.INT
        assert normalize_type("int64") == TypeClass.INT
        assert normalize_type("float") == TypeClass.DECIMAL
        assert normalize_type("double") == TypeClass.DECIMAL
        assert normalize_type("numeric") == TypeClass.DECIMAL
        assert normalize_type("varchar") == TypeClass.TEXT
        assert normalize_type("string") == TypeClass.TEXT
        assert normalize_type("boolean") == TypeClass.BOOL
        assert normalize_type("datetime") == TypeClass.TIMESTAMP

    def test_case_insensitive(self):
        assert normalize_type("INT") == TypeClass.INT
        assert normalize_type("Varchar") == TypeClass.TEXT
        assert normalize_type("BOOLEAN") == TypeClass.BOOL

    def test_parametrized_types_stripped(self):
        assert normalize_type("decimal(10,2)") == TypeClass.DECIMAL
        assert normalize_type("varchar(255)") == TypeClass.TEXT

    def test_unknown_type_returns_any(self):
        assert normalize_type("custom_type_xyz") == TypeClass.ANY
        assert normalize_type("") == TypeClass.ANY

    def test_wildcard_returns_any(self):
        assert normalize_type("*") == TypeClass.ANY
        assert normalize_type("any") == TypeClass.ANY


class TestCompatibility:
    def test_identical_types(self):
        assert check_compatibility(TypeClass.INT, TypeClass.INT) == Compatibility.IDENTICAL
        assert check_compatibility(TypeClass.TEXT, TypeClass.TEXT) == Compatibility.IDENTICAL

    def test_any_is_always_identical(self):
        assert check_compatibility(TypeClass.ANY, TypeClass.INT) == Compatibility.IDENTICAL
        assert check_compatibility(TypeClass.INT, TypeClass.ANY) == Compatibility.IDENTICAL
        assert check_compatibility(TypeClass.ANY, TypeClass.ANY) == Compatibility.IDENTICAL

    def test_widening_pairs(self):
        assert check_compatibility(TypeClass.INT, TypeClass.DECIMAL) == Compatibility.WIDENING
        assert check_compatibility(TypeClass.DATE, TypeClass.TIMESTAMP) == Compatibility.WIDENING
        assert check_compatibility(TypeClass.BOOL, TypeClass.INT) == Compatibility.WIDENING

    def test_breaking_pairs(self):
        assert check_compatibility(TypeClass.DECIMAL, TypeClass.BOOL) == Compatibility.BREAKING
        assert check_compatibility(TypeClass.DATE, TypeClass.INT) == Compatibility.BREAKING
        assert check_compatibility(TypeClass.TEXT, TypeClass.INT) == Compatibility.BREAKING
