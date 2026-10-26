"""Cooperative cancellation plus interruption of one owned DuckDB connection."""
from contextlib import contextmanager
import threading
import time


class ExecutionStopped(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class ExecutionControl:
    def __init__(self, deadline):
        self.deadline = deadline
        self._lock = threading.RLock()
        self._reason = None
        self._conn = None

    def stop(self, reason='cancelled'):
        with self._lock:
            if self._reason is None:
                self._reason = reason
            if self._conn is not None:
                self._conn.interrupt()

    def check(self):
        with self._lock:
            if self._reason is None and time.monotonic() >= self.deadline:
                self._reason = 'timed_out'
            if self._reason:
                raise ExecutionStopped(self._reason)

    @contextmanager
    def workspace_lock(self, lock):
        # A queued query must remain cancellable while another operation owns the workspace.
        while True:
            self.check()
            if lock.acquire(timeout=0.05):
                break
        try:
            self.check()
            yield
        finally:
            lock.release()

    @contextmanager
    def connection(self, conn):
        with self._lock:
            self.check()
            self._conn = conn
        try:
            yield _Connection(conn, self)
            self.check()
        except Exception:
            self.check()  # Translate a DuckDB interrupt into the original terminal reason.
            raise
        finally:
            # Serialize detach with interrupt so no timer can touch a closed connection.
            with self._lock:
                self._conn = None


class _Connection:
    def __init__(self, conn, control):
        self._conn, self._control = conn, control

    def execute(self, *args, **kwargs):
        self._control.check()
        self._conn.execute(*args, **kwargs)
        self._control.check()
        return self

    def fetchone(self):
        self._control.check()
        value = self._conn.fetchone()
        self._control.check()
        return value

    def fetchall(self):
        self._control.check()
        value = self._conn.fetchall()
        self._control.check()
        return value
