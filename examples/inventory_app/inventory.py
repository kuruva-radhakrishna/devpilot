"""Inventory checks for the sample warehouse service.

Deliberate bug for the DevPilot benchmark: can_fulfill() uses a strict greater-
than, so an order that exactly matches available stock is wrongly rejected
(off-by-one / wrong comparison operator).
"""


def can_fulfill(stock, requested):
    # BUG: should be >= ; exact-stock orders are rejected.
    return stock > requested
