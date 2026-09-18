PRODUCTS = {"a": 100, "b": 250}


def price_of(sku):
    return PRODUCTS.get(sku, 0)
