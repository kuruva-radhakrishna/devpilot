import pytest
from paths import safe_read


def test_normal():
    assert safe_read("/data", "file.txt").replace("\\", "/") == "/data/file.txt"


def test_traversal_blocked():
    with pytest.raises(ValueError):
        safe_read("/data", "../secret.txt")
