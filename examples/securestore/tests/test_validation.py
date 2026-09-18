import pytest
from validation import set_quantity


def test_valid():
    assert set_quantity(3) == 3


def test_negative_rejected():
    with pytest.raises(ValueError):
        set_quantity(-1)
