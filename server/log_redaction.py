from __future__ import annotations

"""Keep the OTLP path token out of the server's access log."""

import logging
import re

_OTLP_TOKEN = re.compile(r"(/agent-ingest/otlp/[^/\s]+/)[^/\s?]+")


def redact(text: str) -> str:
    return _OTLP_TOKEN.sub(r"\1[redacted]", text)


class RedactOtlpPathToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install() -> None:
    """Attach to uvicorn's access logger; uvicorn's logging setup keeps logger filters."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactOtlpPathToken) for f in logger.filters):
        logger.addFilter(RedactOtlpPathToken())
