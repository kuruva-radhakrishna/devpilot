from loyalty import points_for


def test_points():
    assert points_for(95) == 9
