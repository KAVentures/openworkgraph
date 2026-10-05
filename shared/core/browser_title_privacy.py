from __future__ import annotations

"""Storage-time protection for window, tab and control titles.

Titles carry most of the context that makes work evidence useful ("Re: Contract
renewal Q4 - Gmail", "Q4 pipeline - Customer tracker - Google Sheets"). They
also often carry personal data. Before anything is stored, every title (browser
tabs and desktop apps alike) keeps its text, and only sensitive details are
replaced with stable tokens:

* people's names -> ``PERSON_x`` (name lexicon plus context cues)
* email addresses and phone numbers -> ``EMAIL_x`` / ``PHONE_x``
* personal identity numbers, IBANs, payment cards, credentials and explicitly
  labelled personal IDs -> typed tokens (``sensitive_identifiers``)

Everything else stays: subjects, document and project names, company and
product names, business references, dates and amounts. Tokens are keyed to this
installation, so the same person gets the same token again ("same person"
without "who"), and already-tokenized text is left unchanged, so this is
idempotent. Browser events also keep the recognised work surface
(``page.surface``) used for workflow grouping.
"""

import copy
from typing import Any

from .browser_utils import is_browser_app
from .normalizer import safe_surface

_TARGET_TEXT_KEYS = ("label", "name", "text", "title", "aria_label", "placeholder")


def _protect_text(text: Any) -> str:
    """Keep the text; tokenize names, contact details and personal identifiers.

    Fails closed: if any privacy pass raises, the text is dropped (stored as an
    empty string) rather than stored partly processed.
    """
    raw = str(text or "")
    if not raw.strip():
        return raw
    try:
        from sensitive_identifiers import redact_sensitive_identifiers
        from server.ai_context import contextual_text_redactor
        from server.privacy_pipeline import redact_for_display_now

        value = redact_sensitive_identifiers(raw, redact_adjacent_name=True)
        value = contextual_text_redactor()(value)
        value = redact_for_display_now(value)
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def protect_path(path: Any) -> str:
    """URL path with name slugs and identifiers tokenized ("/people/anna-svensson")."""
    raw = str(path or "")
    if not raw:
        return raw
    try:
        from server.ai_context import _redact_slug_component

        return "/".join(_redact_slug_component(part, protect_text) for part in raw.split("/"))
    except Exception:
        return ""  # fail closed: never store a path we could not check


def protect_titles_at_rest(event: dict[str, Any], *, cache: dict[str, str] | None = None) -> dict[str, Any]:
    """Apply ``protect_text`` to an event's titles and control labels before storage.

    ``cache`` lets a bulk caller (the upgrade migration) protect each distinct
    text once.
    """
    if cache is not None:
        def protect_text(text: Any) -> str:  # noqa: F811  (cached variant for bulk use)
            key = str(text or "")
            if key not in cache:
                cache[key] = _protect_text(key)
            return cache[key]
    else:
        protect_text = _protect_text
    e = copy.deepcopy(event)
    meta = e.get("metadata") if isinstance(e.get("metadata"), dict) else None
    if str(e.get("source") or (meta or {}).get("source") or "") == "agent":
        return e  # agent evidence carries no titles; its fields are allowlisted separately
    app = str(e.get("app") or "")
    original_title = str(e.get("window_title") or "")
    if original_title:
        e["window_title"] = protect_text(original_title)
    if meta is None:
        return e
    meta = copy.deepcopy(meta)
    page = meta.get("page") if isinstance(meta.get("page"), dict) else None
    if page is not None:
        page = dict(page)
        raw_page_title = str(page.get("title") or original_title or "")
        if is_browser_app(app) or page.get("hostname"):
            # The recognised work surface (Gmail, Salesforce, ...) for grouping.
            page["surface"] = safe_surface(
                app=app, hostname=str(page.get("hostname") or ""),
                pathname=str(page.get("pathname") or ""), title=raw_page_title,
            ) or page.get("surface") or "Browser"
        if page.get("title"):
            page["title"] = protect_text(page["title"])
        if page.get("pathname"):
            page["pathname"] = protect_path(page["pathname"])
        meta["page"] = page
    target = meta.get("target") if isinstance(meta.get("target"), dict) else None
    if target is not None:
        target = dict(target)
        for key in _TARGET_TEXT_KEYS:
            if isinstance(target.get(key), str) and target[key]:
                target[key] = protect_text(target[key])
        meta["target"] = target
    if is_browser_app(app) and not e.get("window_title") and page:
        e["window_title"] = str(page.get("surface") or "")
    privacy = dict(meta.get("privacy") or {}) if isinstance(meta.get("privacy"), dict) else {}
    privacy["titles"] = "kept_with_sensitive_details_tokenized"
    meta["privacy"] = privacy
    e["metadata"] = meta
    return e


protect_text = _protect_text

# Kept for callers of the pre-v0.108 name.
minimize_browser_title_at_rest = protect_titles_at_rest

__all__ = ["minimize_browser_title_at_rest", "protect_path", "protect_text", "protect_titles_at_rest"]
