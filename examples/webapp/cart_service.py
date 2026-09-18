from catalog import price_of


def cart_value(skus):
    return sum(price_of(s) for s in skus)
