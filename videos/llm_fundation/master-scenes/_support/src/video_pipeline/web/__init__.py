"""Public Web UI service and loopback HTTP seams."""

from video_pipeline.web.server import create_server, serve
from video_pipeline.web.service import JobSnapshot, ServiceLimits, WebService
from video_pipeline.web_errors import (
    NotFoundError,
    QueueFullError,
    RequestValidationError,
    StateConflictError,
)

__all__ = [
    "JobSnapshot",
    "NotFoundError",
    "QueueFullError",
    "RequestValidationError",
    "ServiceLimits",
    "StateConflictError",
    "WebService",
    "create_server",
    "serve",
]
