"""Compatibility alias for :mod:`shared.core.browser_utils`.

New OpenWorkGraph code should import `shared.core.browser_utils` directly. This root
module remains temporarily so existing integrations and older installed code do
not break during the repository-layout migration.
"""
from shared.core import browser_utils as _impl
import sys as _sys

_sys.modules[__name__] = _impl
