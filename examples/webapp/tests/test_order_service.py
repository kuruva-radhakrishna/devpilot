from order_service import can_reserve


def test_reserve_exact():
    assert can_reserve("widget", 5) is True
