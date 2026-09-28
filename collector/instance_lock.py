from __future__ import annotations

"""One recording collector per data directory.

An OS advisory lock on a file inside the data directory is held for the
collector's lifetime. The OS releases it when the process exits or crashes, so
there is no stale PID file to clean up. ``data/live`` and ``data/demo`` are
different directories and therefore different locks.
"""

import os
from pathlib import Path

LOCK_NAME = ".collector.lock"
# Exit code for "another collector already records this data directory".
EXIT_ALREADY_RUNNING = 75


class CollectorLock:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / LOCK_NAME
        self._handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))  # informational only
            handle.flush()
        except OSError:
            pass
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

    def __enter__(self) -> "CollectorLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
