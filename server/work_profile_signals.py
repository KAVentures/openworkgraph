from __future__ import annotations

"""Compatibility alias; canonical implementation lives in server.work_profile.signals."""

import sys as _sys
from .work_profile import signals as _impl

_sys.modules[__name__] = _impl
