from money_format import format_amount


def test_positive():
    assert format_amount(123456) == "$1,234.56"


def test_zero():
    assert format_amount(0) == "$0.00"


def test_negative():
    assert format_amount(-500) == "-$5.00"
