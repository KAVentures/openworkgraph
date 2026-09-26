from __future__ import annotations

"""Storage-time minimization for browser window/tab titles.

Browser titles frequently contain person, customer, patient, ticket, document, or
message text. OpenWorkGraph only needs the privacy-safe work surface for workflow
analysis, so browser-container titles are reduced before persistence.
"""

import copy
from typing import Any

from browser_utils import is_browser_app
from normalizer import safe_surface


def minimize_browser_title_at_rest(event: dict[str, Any]) -> dict[str, Any]:
    """Replace arbitrary browser titles with the already-known safe work surface.

    This is intentionally idempotent. Non-browser desktop events and agent events
    are returned unchanged. For browser-extension evidence the raw page title may
    be used once to classify Jira/Confluence/etc., then it is discarded.
    """
    e = copy.deepcopy(event)
    event_type = str(e.get("event_type") or "")
    app = str(e.get("app") or "")
    if not event_type.startswith("browser_") and not is_browser_app(app):
        return e

    meta = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
    meta = copy.deepcopy(meta)
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
    page = dict(page)

    raw_title = str(page.get("title") or e.get("window_title") or "")
    surface = safe_surface(
        app=app,
        hostname=str(page.get("hostname") or ""),
        pathname=str(page.get("pathname") or ""),
        title=raw_title,
    )
    if not surface:
        surface = "Browser"

    e["window_title"] = surface
    if page:
        page["title"] = surface
        page["surface"] = surface
        meta["page"] = page

    privacy = meta.get("privacy") if isinstance(meta.get("privacy"), dict) else {}
    privacy = dict(privacy)
    privacy["arbitrary_browser_title_persisted"] = False
    meta["privacy"] = privacy
    e["metadata"] = meta
    return e


__all__ = ["minimize_browser_title_at_rest"]
