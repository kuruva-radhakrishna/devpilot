def sort_by_price(items):
    # BUG: sorts by name despite the name saying price.
    return sorted(items, key=lambda it: it["name"])
