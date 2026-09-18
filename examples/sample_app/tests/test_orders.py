from orders import summarize_order


def test_with_address():
    order = {"id": 1, "address": {"city": "Bangalore"}}
    assert summarize_order(order) == "Order 1 ships to Bangalore"


def test_missing_address_defaults_to_unknown():
    # An order without an address must NOT crash — it should ship to "unknown".
    # This currently FAILS (KeyError) because of the bug in orders.py.
    order = {"id": 2}
    assert summarize_order(order) == "Order 2 ships to unknown"
