"""Typed public error categories shared by the local Web boundaries."""

from __future__ import annotations


class NotFoundError(ValueError):
    """The requested project, job, revision, or asset does not exist."""


class StateConflictError(ValueError):
    """The requested operation conflicts with the current durable state."""


class QueueFullError(ValueError):
    """The bounded render queue cannot accept another job."""


class RequestValidationError(ValueError):
    """The request or its public identifiers failed validation."""


__all__ = [
    "NotFoundError",
    "RequestValidationError",
    "QueueFullError",
    "StateConflictError",
]
