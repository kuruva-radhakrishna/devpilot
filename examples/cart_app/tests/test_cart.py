from cart import total


def test_no_discount():
    items = [{"price": 10, "qty": 2}, {"price": 5, "qty": 1}]
    assert total(items) == 25


def test_single_discount_applied_once():
    # A 10% discount on 100 should give 90, not 80.
    # Currently FAILS: the discount is applied twice -> 80.
    items = [{"price": 100, "qty": 1}]
    assert total(items, 0.1) == 90
