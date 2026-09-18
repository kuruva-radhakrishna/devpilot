from inventory_repo import available


def can_reserve(sku, qty):
    return available(sku, qty)
