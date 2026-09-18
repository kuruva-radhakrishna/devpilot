"""Shopping-cart totals for the sample store.

Deliberate bug for the DevPilot benchmark: total() subtracts the discount twice
(a regression), so any discounted total is too low.
"""


def subtotal(items):
    return sum(item["price"] * item["qty"] for item in items)


def total(items, discount=0.0):
    base = subtotal(items)
    # BUG: the discount is subtracted twice.
    discounted = base - (base * discount)
    return discounted - (base * discount)
