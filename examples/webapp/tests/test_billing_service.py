from billing_service import charge


def test_charge_at_boundary():
    assert charge(100) == 100.0
