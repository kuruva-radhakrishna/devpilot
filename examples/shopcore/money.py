def format_money(cents):
    # BUG: divides by 10 instead of 100.
    return round(cents / 10, 2)
