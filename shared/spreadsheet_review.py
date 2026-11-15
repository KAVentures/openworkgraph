"""Optional text-oriented CSV projection; canonical exports remain exact."""
import re
from typing import Any

_FORMULA_PREFIX = re.compile(r'^[\s\ufeff]*[=+\-@]')


def review_cell(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if value[0] in '\t\r\n' or _FORMULA_PREFIX.match(value):
        return "'" + value
    return value
