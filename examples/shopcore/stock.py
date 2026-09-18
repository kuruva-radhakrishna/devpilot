def is_in_stock(quantity):
    # BUG: inverted; reports in-stock when quantity is zero.
    return quantity == 0
