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
    """Replace browser-container titles with the known privacy-safe work surface.

    This is intentionally idempotent. Non-browser desktop/business-app evidence
    and agent evidence are returned unchanged. The raw page title may be used once
    to classify Jira/Confluence/etc., then it is discarded before persistence.
    """
    e = copy.deepcopy(event)
    app = str(e.get("app") or "")
    # Actual extension ingestion normalizes its app to Browser/Chrome/etc., and OS
    # tab-title leakage likewise arrives through a browser-container app. Do not
    # strip rich titles from synthetic/direct events whose app is already a native
    # business surface such as Fortnox; that would lose useful non-browser context.
    if not is_browser_app(app):
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
