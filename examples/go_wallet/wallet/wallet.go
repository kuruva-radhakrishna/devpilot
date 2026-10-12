// Package wallet is a tiny integer-cents wallet.
//
// Deliberate bug for the DevPilot benchmark: Withdraw lets the balance go negative
// instead of returning ErrInsufficientFunds.
package wallet

import "errors"

// ErrInsufficientFunds is returned when a withdrawal exceeds the balance.
var ErrInsufficientFunds = errors.New("insufficient funds")

// ErrInvalidAmount is returned for zero or negative amounts.
var ErrInvalidAmount = errors.New("amount must be positive")

// Wallet holds a balance in cents.
type Wallet struct {
	balance int
}

// Balance returns the current balance in cents.
func (w *Wallet) Balance() int {
	return w.balance
}

// Deposit adds amount cents to the wallet.
func (w *Wallet) Deposit(amount int) error {
	if amount <= 0 {
		return ErrInvalidAmount
	}
	w.balance += amount
	return nil
}

// Withdraw removes amount cents from the wallet.
func (w *Wallet) Withdraw(amount int) error {
	if amount <= 0 {
		return ErrInvalidAmount
	}
	// BUG: no check that the wallet can cover the amount.
	w.balance -= amount
	return nil
}
