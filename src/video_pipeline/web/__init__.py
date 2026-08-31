"""Public Web UI service and loopback HTTP seams."""

from video_pipeline.web.server import create_server, serve
from video_pipeline.web.service import (
    JobSnapshot,
    QueueFullError,
    ServiceLimits,
    WebService,
)

__all__ = [
    "JobSnapshot",
    "QueueFullError",
    "ServiceLimits",
    "WebService",
    "create_server",
    "serve",
]
