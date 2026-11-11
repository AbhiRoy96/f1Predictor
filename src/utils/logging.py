"""Logging utilities for the F1 Race Intelligence pipeline."""

import logging
import json
import sys
from datetime import datetime, timezone

from opentelemetry import trace


class JsonFormatter(logging.Formatter):
    """Correlate console logs without including arbitrary LogRecord attributes."""

    def format(self, record: logging.LogRecord) -> str:
        context = trace.get_current_span().get_span_context()
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "severity": record.levelname, "logger": record.name, "message": record.getMessage(),
            "trace_id": format(context.trace_id, "032x") if context.is_valid else None,
            "span_id": format(context.span_id, "016x") if context.is_valid else None,
        }
        payload.update(getattr(record, "telemetry", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logger(name: str = "f1_intelligence", level: int = logging.INFO) -> logging.Logger:
    """Use one root console handler so OTel can export each log exactly once."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        root.setLevel(level)
    return logger
