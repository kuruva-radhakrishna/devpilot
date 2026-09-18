def format_amount(cents):
    # BUG: only handles positive amounts; must handle zero and negatives with a sign.
    dollars = cents / 100
    return f"${dollars:,.2f}"
