from inventory import can_fulfill


def test_more_than_enough_stock():
    assert can_fulfill(10, 3) is True


def test_not_enough_stock():
    assert can_fulfill(2, 5) is False


def test_exact_stock_can_be_fulfilled():
    # Requesting exactly the available stock must succeed.
    # Currently FAILS: `stock > requested` rejects the exact-match case.
    assert can_fulfill(5, 5) is True
