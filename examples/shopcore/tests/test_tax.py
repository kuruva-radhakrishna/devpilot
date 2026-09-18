from tax import add_tax


def test_add_tax():
    assert add_tax(100, 0.1) == 110
