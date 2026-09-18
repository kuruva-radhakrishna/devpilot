PRICES_CENTS = {"book": 1200, "pen": 150}


def price_of(sku):
    # BUG: returns cents; callers expect dollars.
    return PRICES_CENTS.get(sku, 0)
