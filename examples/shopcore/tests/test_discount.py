from discount import apply_discount


def test_apply_discount():
    assert apply_discount(200, 10) == 180
