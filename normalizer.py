from __future__ import annotations

"""Privacy-safe operational normalization.

Raw evidence remains untouched. This module derives a content-minimized
representation for task inference, MCP/API, and long-lived operational analytics.
Identity fields are preserved so multi-device/actor data never collapses together.
"""

import hashlib
import re
from typing import Any

from browser_utils import is_browser_app


def _clean(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _pseudonym(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:8]


def safe_surface(*, app: str = "", hostname: str = "", pathname: str = "", title: str = "") -> str:
    host = _clean(hostname).lower().strip(".")
    path = _clean(pathname, 300).lower()
    low_title = _clean(title).lower()

    if host in {"chatgpt.com", "chat.openai.com"} or "chatgpt" in low_title:
        return "ChatGPT"
    if host.endswith("lovable.dev") or "lovable" in low_title:
        return "Lovable"
    if host in {"github.com", "www.github.com"} or host.endswith(".github.com") or "github" in low_title:
        return "GitHub"
    if host == "mail.google.com" or "gmail" in low_title:
        return "Gmail"
    if host == "docs.google.com":
        if path.startswith("/spreadsheets") or "google sheets" in low_title:
            return "Google Sheets"
        if path.startswith("/document") or "google docs" in low_title:
            return "Google Docs"
        if path.startswith("/presentation") or "google slides" in low_title:
            return "Google Slides"
        if path.startswith("/forms") or "google forms" in low_title:
            return "Google Forms"
        return "Google Workspace"
    if host == "drive.google.com" or "google drive" in low_title:
        return "Google Drive"
    if host.endswith("notion.so") or host == "notion.com" or host.endswith(".notion.com") or "notion" in low_title:
        return "Notion"
    if host.endswith("slack.com") or "slack" in low_title:
        return "Slack"
    if host in {"teams.microsoft.com", "teams.cloud.microsoft"} or "microsoft teams" in low_title:
        return "Microsoft Teams"
    if host in {"outlook.office.com", "outlook.office365.com", "outlook.live.com"} or "outlook" in low_title:
        return "Outlook"
    if host.endswith("sharepoint.com") or "sharepoint" in low_title:
        return "SharePoint"
    if host in {"office.com", "www.office.com", "microsoft365.com", "www.microsoft365.com"}:
        return "Microsoft 365"
    if host.endswith("figma.com") or "figma" in low_title:
        return "Figma"
    if host.endswith("linear.app") or re.search(r"\blinear\b", low_title):
        return "Linear"
    if host.endswith("atlassian.net"):
        if "confluence" in low_title:
            return "Confluence"
        if "jira" in low_title:
            return "Jira"
        return "Atlassian"
    if host.endswith("salesforce.com") or host.endswith("force.com") or "salesforce" in low_title:
        return "Salesforce"
    if host.endswith("hubspot.com") or "hubspot" in low_title:
        return "HubSpot"

    markers = [
        ("google docs", "Google Docs"), ("google sheets", "Google Sheets"),
        ("google slides", "Google Slides"), ("google drive", "Google Drive"),
        ("gmail", "Gmail"), ("chatgpt", "ChatGPT"), ("github", "GitHub"),
        ("lovable", "Lovable"), ("salesforce", "Salesforce"), ("hubspot", "HubSpot"),
        ("notion", "Notion"), ("figma", "Figma"), ("slack", "Slack"),
        ("microsoft teams", "Microsoft Teams"), ("outlook", "Outlook"),
        ("sharepoint", "SharePoint"),
    ]
    for marker, label in markers:
        if marker in low_title:
            return label

    if app and not is_browser_app(app):
        return _clean(app, 120) or "Unknown"
    if host:
        return f"Web app {_pseudonym(host)}"
    return "Browser" if is_browser_app(app) else (_clean(app, 120) or "Unknown")


_ACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*create\s+(new\s+)?repository\b|^\s*create\s+repo\b", re.I), "Create repository"),
    (re.compile(r"^\s*new\s+repository\b|^\s*new\s+repo\b", re.I), "New repository"),
    (re.compile(r"^\s*(submit\s+(new\s+)?issue|create\s+issue)\b", re.I), "Submit new issue"),
    (re.compile(r"^\s*new\s+issue\b", re.I), "New issue"),
    (re.compile(r"^\s*approve\s+invoice\b", re.I), "Approve invoice"),
    (re.compile(r"^\s*(complete|finish)\s+case\b", re.I), "Complete case"),
    (re.compile(r"^\s*upload\s+attachment\b", re.I), "Upload attachment"),
    (re.compile(r"^\s*confirm\s+details\b", re.I), "Confirm details"),
    (re.compile(r"^\s*compose\b|^\s*new\s+(message|email)\b", re.I), "Compose"),
    (re.compile(r"^\s*reply\b", re.I), "Reply"),
    (re.compile(r"^\s*forward\b", re.I), "Forward"),
    (re.compile(r"^\s*send\b", re.I), "Send"),
    (re.compile(r"^\s*save\b", re.I), "Save"),
    (re.compile(r"^\s*submit\b", re.I), "Submit"),
    (re.compile(r"^\s*publish\b", re.I), "Publish"),
    (re.compile(r"^\s*approve\b", re.I), "Approve"),
    (re.compile(r"^\s*(reject|decline)\b", re.I), "Reject"),
    (re.compile(r"^\s*(complete|finish)\b", re.I), "Complete"),
    (re.compile(r"^\s*merge\b", re.I), "Merge"),
    (re.compile(r"^\s*deploy\b", re.I), "Deploy"),
    (re.compile(r"^\s*upload\b", re.I), "Upload"),
    (re.compile(r"^\s*download\b", re.I), "Download"),
    (re.compile(r"^\s*confirm\b", re.I), "Confirm"),
    (re.compile(r"^\s*invite\b", re.I), "Invite"),
    (re.compile(r"^\s*search\b", re.I), "Search"),
    (re.compile(r"^\s*(sign\s*in|log\s*in)\b", re.I), "Sign in"),
]


def safe_action_label(target: dict[str, Any] | None) -> str:
    target = target or {}
    fields = [target.get(k) for k in ("label", "title", "description", "help", "name", "identifier")]
    text = " | ".join(_clean(v, 180) for v in fields if _clean(v, 180))
    if not text:
        return ""
    if re.search(r"password|passcode|one[- ]?time|\botp\b|security code|credit card", text, re.I):
        return ""
    for pattern, label in _ACTION_PATTERNS:
        if pattern.search(text):
            return label
    return ""


def _safe_target(target: dict[str, Any] | None) -> dict[str, Any]:
    target = dict(target or {})
    out: dict[str, Any] = {}
    for key in ("tag", "role", "subrole", "type"):
        value = _clean(target.get(key), 80)
        if value:
            out[key] = value
    if target.get("secure"):
        out["secure"] = True
    if target.get("contenteditable"):
        out["contenteditable"] = True
    label = safe_action_label(target)
    if label:
        out["label"] = label
        out["title"] = label
    return out


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    e = dict(event)
    meta = dict(e.get("metadata") or {})
    page = dict(meta.get("page") or {}) if isinstance(meta.get("page"), dict) else {}
    raw_app = _clean(e.get("app"), 160) or "Unknown"
    raw_title = _clean(e.get("window_title"), 800)
    surface = safe_surface(
        app=raw_app,
        hostname=str(page.get("hostname") or ""),
        pathname=str(page.get("pathname") or ""),
        title=str(page.get("title") or raw_title),
    )

    normalized_meta: dict[str, Any] = {
        "source": e.get("source") or meta.get("source") or "desktop",
        "container_app": raw_app,
        "normalized": True,
        "privacy": {
            "raw_evidence_separate": True,
            "typed_values": False,
            "arbitrary_visible_text": False,
            "full_url_paths": False,
        },
    }
    if isinstance(meta.get("activity"), dict):
        normalized_meta["activity"] = {
            k: v for k, v in meta["activity"].items()
            if k in {"foreground_seconds", "engaged_seconds", "idle_seconds", "active_input_seconds", "keypress_count", "click_count", "scroll_count", "input_events"}
        }

    action = _clean(meta.get("action"), 80)
    if action:
        normalized_meta["action"] = action
    for key in ("button", "dx", "dy", "excluded", "change_detection"):
        if key in meta:
            normalized_meta[key] = meta[key]

    target = _safe_target(meta.get("target") if isinstance(meta.get("target"), dict) else {})
    if target:
        normalized_meta["target"] = target

    if page or str(e.get("event_type") or "").startswith("browser_"):
        normalized_meta["page"] = {"title": surface, "surface": surface}

    return {
        "event_id": str(e.get("event_id") or ""),
        "observed_at": str(e.get("observed_at") or ""),
        "schema_version": str(e.get("schema_version") or "1.0"),
        "organization_id": str(e.get("organization_id") or ""),
        "actor_id": str(e.get("actor_id") or ""),
        "device_id": str(e.get("device_id") or ""),
        "sensor_id": str(e.get("sensor_id") or ""),
        "source": str(e.get("source") or meta.get("source") or "desktop"),
        "session_id": str(e.get("session_id") or ""),
        "app": surface,
        "window_title": surface,
        "event_type": str(e.get("event_type") or "unknown"),
        "duration_seconds": float(e.get("duration_seconds") or 0),
        "screenshot_path": None,
        "metadata": normalized_meta,
    }
