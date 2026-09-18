def apply_discount(price, percent):
    # BUG: subtracts the percent value, not percent OF price.
    return price - percent
