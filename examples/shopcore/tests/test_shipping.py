from shipping import shipping_cost


def test_free_at_threshold():
    assert shipping_cost(10) == 0


def test_paid_below():
    assert shipping_cost(5) == 5
