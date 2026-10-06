"""Compatibility alias for :mod:`shared.core.sensitive_identifiers`.

New OpenWorkGraph code should import `shared.core.sensitive_identifiers` directly. This root
module remains temporarily so existing integrations and older installed code do
not break during the repository-layout migration.
"""
from shared.core import sensitive_identifiers as _impl
import sys as _sys

_sys.modules[__name__] = _impl
