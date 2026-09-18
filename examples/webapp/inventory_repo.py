STOCK = {"widget": 5}


def available(sku, qty):
    # BUG: strict > rejects reserving the exact remaining stock.
    return STOCK.get(sku, 0) > qty
