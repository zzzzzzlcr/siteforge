package internal

import (
	"testing"
)

func TestDisconnect_InvokesCancelFuncs(t *testing.T) {
	cancelCalled := false
	allocCancelCalled := false

	c := &Client{
		cancel:      func() { cancelCalled = true },
		allocCancel: func() { allocCancelCalled = true },
	}

	c.Disconnect()

	if !cancelCalled {
		t.Error("Disconnect() should invoke cancel")
	}
	if !allocCancelCalled {
		t.Error("Disconnect() should invoke allocCancel")
	}
}

func TestDisconnect_NilCancelSafe(t *testing.T) {
	allocCancelCalled := false

	c := &Client{
		cancel:      nil,
		allocCancel: func() { allocCancelCalled = true },
	}

	// Should not panic
	c.Disconnect()

	if !allocCancelCalled {
		t.Error("Disconnect() should still invoke allocCancel when cancel is nil")
	}
}

func TestDisconnect_NilAllocCancelSafe(t *testing.T) {
	cancelCalled := false

	c := &Client{
		cancel:      func() { cancelCalled = true },
		allocCancel: nil,
	}

	// Should not panic
	c.Disconnect()

	if !cancelCalled {
		t.Error("Disconnect() should still invoke cancel when allocCancel is nil")
	}
}

func TestDisconnect_AllNilSafe(t *testing.T) {
	c := &Client{
		cancel:      nil,
		allocCancel: nil,
	}

	// Should not panic
	c.Disconnect()
}
