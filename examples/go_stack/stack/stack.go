// Package stack has a LIFO Stack and a FIFO Queue. Both have a Len method, so a repair
// has to name the method as Stack.Pop / Queue.Len rather than a bare "Len".
//
// Deliberate bug for the DevPilot benchmark: Stack.Pop panics on an empty stack instead
// of returning (0, false).
package stack

// Stack is a last-in, first-out stack of ints.
type Stack struct {
	items []int
}

// Push adds x to the top of the stack.
func (s *Stack) Push(x int) {
	s.items = append(s.items, x)
}

// Pop removes and returns the top of the stack. ok is false when the stack is empty.
func (s *Stack) Pop() (int, bool) {
	// BUG: indexes items[-1] when the stack is empty, which panics.
	v := s.items[len(s.items)-1]
	s.items = s.items[:len(s.items)-1]
	return v, true
}

// Peek returns the top of the stack without removing it.
func (s *Stack) Peek() (int, bool) {
	if len(s.items) == 0 {
		return 0, false
	}
	return s.items[len(s.items)-1], true
}

// Len reports how many items are on the stack.
func (s *Stack) Len() int {
	return len(s.items)
}

// Queue is a first-in, first-out queue of ints.
type Queue struct {
	items []int
}

// Enqueue adds x to the back of the queue.
func (q *Queue) Enqueue(x int) {
	q.items = append(q.items, x)
}

// Len reports how many items are queued.
func (q *Queue) Len() int {
	return len(q.items)
}
