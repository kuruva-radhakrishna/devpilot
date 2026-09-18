"""Account operations for the sample bank service.

Deliberate bug for the DevPilot benchmark: withdraw() does not reject
overdrafts, so a withdrawal larger than the balance silently returns a negative
balance instead of raising.
"""


def withdraw(balance, amount):
    # BUG: no overdraft check — should reject amount > balance.
    return balance - amount


def deposit(balance, amount):
    if amount <= 0:
        raise ValueError("deposit must be positive")
    return balance + amount
