from currency import to_cents


def test_to_cents_rounding():
    assert to_cents(1.15) == 115
