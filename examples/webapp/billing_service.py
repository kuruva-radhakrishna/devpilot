from rates import rate_for


def charge(units):
    return units * rate_for(units)
