import pytest
from password_rules import validate


def test_good():
    assert validate("Abcdef12") is True


def test_short():
    with pytest.raises(ValueError):
        validate("Ab1")


def test_needs_digit():
    with pytest.raises(ValueError):
        validate("Abcdefgh")


def test_needs_upper():
    with pytest.raises(ValueError):
        validate("abcdef12")
