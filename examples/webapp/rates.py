def rate_for(units):
    # BUG: boundary wrong; 100 units should still be the low rate.
    if units < 100:
        return 1.0
    return 2.0
