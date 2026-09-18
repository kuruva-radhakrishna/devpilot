"""Order utilities for the sample shop service.

Contains a deliberate bug for the DevPilot repair demo: get_order_city assumes
every order has an address, so an order without one raises KeyError instead of
degrading gracefully. The test suite encodes the intended behavior.
"""


def get_order_city(order):
    # BUG: assumes order["address"] always exists.
    return order["address"]["city"]


def summarize_order(order):
    city = get_order_city(order)
    return f"Order {order['id']} ships to {city}"
