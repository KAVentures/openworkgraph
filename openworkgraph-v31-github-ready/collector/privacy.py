from __future__ import annotations

import re
from typing import Iterable


def should_exclude(app: str, title: str, excluded_apps: Iterable[str], title_patterns: Iterable[str]) -> bool:
    app_lower = (app or "").lower()
    if any(x.lower() in app_lower for x in excluded_apps if x):
        return True
    for pattern in title_patterns:
        try:
            if re.search(pattern, title or "", flags=re.IGNORECASE):
                return True
        except re.error:
            if pattern.lower() in (title or "").lower():
                return True
    return False


def title_for_mode(title: str, mode: str) -> str:
    if mode == "none":
        return ""
    if mode == "hash":
        import hashlib
        return hashlib.sha256(title.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return title
