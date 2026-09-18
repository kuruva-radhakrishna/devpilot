from stock import is_in_stock


def test_in_stock():
    assert is_in_stock(3) is True


def test_out_of_stock():
    assert is_in_stock(0) is False
