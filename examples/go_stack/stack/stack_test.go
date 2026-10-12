package stack

import "testing"

func TestPushPop(t *testing.T) {
	s := &Stack{}
	s.Push(1)
	s.Push(2)
	if v, ok := s.Pop(); !ok || v != 2 {
		t.Fatalf("Pop() = %d, %v; want 2, true", v, ok)
	}
	if s.Len() != 1 {
		t.Fatalf("Len() = %d; want 1", s.Len())
	}
}

func TestPopOnEmptyStackReportsNotOK(t *testing.T) {
	s := &Stack{}
	v, ok := s.Pop()
	if ok || v != 0 {
		t.Fatalf("Pop() on empty = %d, %v; want 0, false", v, ok)
	}
}

func TestPeekOnEmptyStack(t *testing.T) {
	if _, ok := (&Stack{}).Peek(); ok {
		t.Fatal("Peek() on empty reported ok")
	}
}

func TestQueueLen(t *testing.T) {
	q := &Queue{}
	q.Enqueue(7)
	q.Enqueue(8)
	if q.Len() != 2 {
		t.Fatalf("Len() = %d; want 2", q.Len())
	}
}
