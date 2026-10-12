package wallet

import (
	"errors"
	"testing"
)

func TestDepositAndWithdraw(t *testing.T) {
	w := &Wallet{}
	if err := w.Deposit(500); err != nil {
		t.Fatal(err)
	}
	if err := w.Withdraw(200); err != nil {
		t.Fatal(err)
	}
	if w.Balance() != 300 {
		t.Fatalf("Balance() = %d; want 300", w.Balance())
	}
}

func TestWithdrawMoreThanTheBalanceIsRefused(t *testing.T) {
	w := &Wallet{}
	_ = w.Deposit(100)
	err := w.Withdraw(150)
	if !errors.Is(err, ErrInsufficientFunds) {
		t.Fatalf("Withdraw(150) error = %v; want ErrInsufficientFunds", err)
	}
	if w.Balance() != 100 {
		t.Fatalf("a refused withdrawal changed the balance to %d", w.Balance())
	}
}

func TestNonPositiveAmountsAreRefused(t *testing.T) {
	w := &Wallet{}
	if !errors.Is(w.Deposit(0), ErrInvalidAmount) || !errors.Is(w.Withdraw(-5), ErrInvalidAmount) {
		t.Fatal("non-positive amounts must return ErrInvalidAmount")
	}
}
