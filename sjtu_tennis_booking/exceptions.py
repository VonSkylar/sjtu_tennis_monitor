"""Custom exception classes for the SJTU Tennis Court Booking Monitor."""

from __future__ import annotations


class BookingPageNotReady(RuntimeError):
    """Raised when the browser booking page is not in the expected state."""
    pass


class RequestRateLimited(RuntimeError):
    """Raised when the school system reports daily request limit exceeded."""
    pass


class PCAppNotReady(RuntimeError):
    """Raised when the PC/ADB automation cannot reach the app."""
    pass


class PCRateLimited(RuntimeError):
    """Raised when the PC version detects the daily request limit."""
    pass
