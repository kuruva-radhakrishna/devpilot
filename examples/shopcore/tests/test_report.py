from report import average_rating


def test_average():
    assert average_rating([4, 4, 4]) == 4.0
