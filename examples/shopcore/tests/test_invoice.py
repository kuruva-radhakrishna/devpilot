from invoice import line_total


def test_line_total():
    assert line_total(500) == 5.0
