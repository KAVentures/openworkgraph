"""Keep approved source-read URLs and provider metadata out of transport logs."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging

_private_source=ContextVar('owg_private_source_http',default=False)


class _PrivateSourceLogs(logging.Filter):
    def filter(self,record):return not _private_source.get()


@contextmanager
def private_source_http_logs():
    # Context-local to the current source task/thread. Unrelated HTTP requests
    # preserve their logging; selected IDs and response headers must not become
    # debug-log evidence or leak through the HTTPX INFO URL.
    for name in ('httpx','httpcore.connection','httpcore.http11','httpcore.http2','httpcore.proxy','httpcore.socks'):
        logger=logging.getLogger(name)
        if not any(isinstance(item,_PrivateSourceLogs) for item in logger.filters):logger.addFilter(_PrivateSourceLogs())
    scope=_private_source.set(True)
    try:yield
    finally:_private_source.reset(scope)
