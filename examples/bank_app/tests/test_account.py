import pytest

from account import deposit, withdraw


def test_normal_withdraw():
    assert withdraw(100, 40) == 60


def test_deposit():
    assert deposit(100, 50) == 150


def test_overdraft_is_rejected():
    # Withdrawing more than the balance must raise ValueError, not go negative.
    # Currently FAILS: withdraw returns -50 instead of raising.
    with pytest.raises(ValueError):
        withdraw(100, 150)
