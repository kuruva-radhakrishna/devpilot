def to_cents(dollars):
    # BUG: float truncation loses a cent (1.15 -> 114).
    return int(dollars * 100)
