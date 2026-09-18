def shipping_cost(weight):
    # BUG: free shipping should apply at exactly 10kg too (>=).
    if weight > 10:
        return 0
    return 5
