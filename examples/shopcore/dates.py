def days_between(start, end):
    # BUG: off-by-one, includes an extra day.
    return end - start + 1
