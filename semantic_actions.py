"""Compatibility alias for :mod:`shared.core.semantic_actions`.

New OpenWorkGraph code should import `shared.core.semantic_actions` directly. This root
module remains temporarily so existing integrations and older installed code do
not break during the repository-layout migration.
"""
from shared.core import semantic_actions as _impl
import sys as _sys

_sys.modules[__name__] = _impl
