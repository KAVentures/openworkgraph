"""Fail-closed privacy projection for the public OpenWorkGraph MCP.

Local SQLite and the Gateway's canonical evidence are never rewritten here.
No OAuth client may request an unredacted metadata blob: even "rich" returns
only known structural evidence fields, with personal details tokenized.
The hosted server must not import local AI settings/key files, which do not
belong to a multi-tenant Vercel function.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from server.contextual_redaction import Detector

_EMAIL = re.compile(r"(?<![\w.])([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})(?![\w.])")
_SWEDISH_ID = re.compile(r"\b(?:\d{8}[-+]?\d{4}|\d{6}[-+]\d{4})\b")
_PHONE = re.compile(r"(?<![\w])(?:\+\d{1,3}[\s.()-]*)?(?:\d[\s.()-]*){9,15}(?![\w])")
_CREDENTIAL = re.compile(r"\b(?:Bearer\s+[A-Za-z0-9._~-]{10,}|(?:sk|ghp|gho|github_pat|glpat)_[A-Za-z0-9_-]{15,})", re.I)
_URL = re.compile(r"https?://[^\s<>]+", re.I)
_META_SIMPLE = frozenset({
    "action", "semantic_action", "semantic_action_confidence",
    "tab_id", "tab_context_id", "browser_session_id",
    "duration_seconds", "click_count", "scroll_count", "keypress_count",
    "foreground_seconds", "engaged_seconds", "active_input_seconds",
    "idle_seconds", "observation_level", "outcome_status",
})
_META_TARGET = frozenset({"role", "tag", "localized_role", "label", "title"})
_META_PAGE = frozenset({"hostname"})
_META_REFERENCE = frozenset({"resource_ref", "file_ref", "provider", "resource_kind", "host", "resolution"})
_DROP_FIELDS = frozenset({
    "prompt", "prompt_text", "raw_prompt", "user_prompt", "system_prompt",
    "raw_text", "raw_data", "raw_message", "message_body", "message_content",
    "customer_email", "person_name", "user_input", "assistant_output",
    "shared_context_terms",
    "messages", "message", "message_text", "content", "raw_content",
    "input", "input_text", "output", "output_text", "completion",
    "transcript", "tool_input", "tool_output", "tool_arguments",
    "arguments", "args", "request_body", "response_body", "payload",
    "clipboard_text", "clipboard_content", "typed_text", "typed_value",
    "password", "secret", "api_key", "auth_token", "access_token",
    "refresh_token", "authorization", "headers", "cookies",
    "resolver_locator", "resource_locator", "pathname", "page_path",
    "url", "href", "full_url", "local_path", "file_path", "screenshot_path",
})
_EVENT_FIELDS = (
    "event_id", "observed_at", "app", "window_title", "event_type",
    "source", "session_id", "duration_seconds", "action", "target_label",
    "target_role", "page_host", "semantic_action", "semantic_action_confidence",
)
_IDS = frozenset({
    "event_id", "session_id", "execution_id", "family_key", "resource_ref",
    "file_ref", "candidate_cluster_id", "candidate_id", "trace_id", "run_id",
    "browser_session_id", "tab_context_id",
})
_MAX_TEXT = 240


class PublicRedactor:
    def __init__(self, *, secret: str, principal: str):
        if not secret or not principal:
            raise ValueError("public redaction requires a scoped secret")
        self.key = hmac.new(secret.encode(), principal.encode(), hashlib.sha256).digest()
        self.person_detector = Detector(token=self._token)

    def _token(self, kind: str, value: str) -> str:
        digest = hmac.new(self.key, value.casefold().strip().encode(), hashlib.sha256).hexdigest()[:10].upper()
        return f"{kind}_{digest}"

    def text(self, value: str) -> str:
        text = str(value)
        text = _CREDENTIAL.sub(lambda m: self._token("SECRET", m.group(0)), text)
        text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = _SWEDISH_ID.sub(lambda m: self._token("PERSONNUMMER", m.group(0)), text)
        # Avoid mistaking timestamps and stable ID tokens for telephone numbers.
        text = _PHONE.sub(
            lambda m: self._token("PHONE", m.group(0))
            if len(re.sub(r"\D", "", m.group(0))) >= 10
            and not re.search(r"\d{4}-\d\d-\d\d", m.group(0))
            else m.group(0),
            text,
        )
        # URLs with arbitrary paths/queries can carry names, tokens or email
        # addresses. Do not reconstruct them from source data in public MCP.
        text = _URL.sub("[LINK_WITHHELD]", text)
        text = self.person_detector.redact(text)
        return text[:_MAX_TEXT] + "…[truncated]" if len(text) > _MAX_TEXT else text

    def metadata(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        output: dict[str, Any] = {}
        for key in _META_SIMPLE:
            value = raw.get(key)
            if isinstance(value, (bool, int, float)):
                if value not in (0, 0.0, False):
                    output[key] = value
            elif isinstance(value, str) and value and key not in _DROP_FIELDS:
                output[key] = self.text(value)
        for key, allowed in (("target", _META_TARGET), ("page", _META_PAGE),
                             ("resource_reference", _META_REFERENCE),
                             ("file_reference", _META_REFERENCE)):
            child = raw.get(key)
            if not isinstance(child, dict):
                continue
            safe = {k: self.text(v) if isinstance(v, str) else v
                    for k, v in child.items() if k in allowed
                    and isinstance(v, (str, int, float, bool)) and v not in (None, "", 0, False)}
            if safe:
                output[key] = safe
        return output

    def event(self, raw: dict[str, Any], *, detail: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        keys = _EVENT_FIELDS if detail == "rich" else (
            "event_id", "observed_at", "app", "window_title",
            "event_type", "source", "session_id", "action", "target_label",
            "page_host", "semantic_action",
        )
        for key in keys:
            value = raw.get(key)
            if value in (None, "", 0, False, [], {}):
                continue
            result[key] = value if isinstance(value, (bool, int, float)) else (
                self.text(str(value))
                if key in {"event_id", "session_id"} and (
                    len(str(value)) > 160 or "@" in str(value) or "/" in str(value)
                )
                else str(value) if key in _IDS or key == "observed_at"
                else self.text(str(value))
            )
        meta = self.metadata(raw.get("metadata"))
        if detail == "rich":
            if meta:
                result["metadata"] = meta
        else:
            # Pull just the structural anchor; no duplicate page/target labels
            # or arbitrary nested content in the default response.
            reference = meta.get("resource_reference")
            if isinstance(reference, dict) and reference.get("resource_ref"):
                result["resource_ref"] = reference["resource_ref"]
        return result

    def visit(self, value: Any, *, detail: str, key: str = "") -> Any:
        if isinstance(value, dict):
            if "event_id" in value and "observed_at" in value and "event_type" in value:
                return self.event(value, detail=detail)
            if key == "metadata":
                return self.metadata(value) if detail == "rich" else {}
            result = {}
            for child_key, child in value.items():
                name = str(child_key)
                if name.casefold() in _DROP_FIELDS or name in {
                    "organization_id", "actor_id", "device_id", "sensor_id",
                }:
                    continue
                if name == "desktop_install_url":
                    # Trusted server-authored onboarding constant, not observed
                    # page content; retain the link for first-time connections.
                    if child == "https://owg.kinvectum.com/connect":
                        result[name] = child
                    continue
                safe = self.visit(child, detail=detail, key=name)
                if safe not in (None, "", [], {}):
                    result[name] = safe
            return result
        if isinstance(value, list):
            return [self.visit(x, detail=detail, key=key) for x in value]
        if isinstance(value, tuple):
            return [self.visit(x, detail=detail, key=key) for x in value]
        if isinstance(value, str):
            return value if key in _IDS or key in {"observed_at", "snapshot_until", "next_cursor"} else self.text(value)
        return value


def project(value: dict[str, Any], *, secret: str, principal: str, detail: str = "compact") -> dict[str, Any]:
    if detail not in {"compact", "rich"}:
        raise ValueError("detail must be compact or rich")
    safe = PublicRedactor(secret=secret, principal=principal).visit(value, detail=detail)
    if not isinstance(safe, dict):
        raise ValueError("hosted projection requires an object")
    if safe.get("data_layer") == "privacy_hardened_raw_rich_evidence":
        safe["data_layer"] = "hosted_redacted_evidence_projection"
    evidence_contract = safe.get("evidence_contract")
    if isinstance(evidence_contract, dict):
        evidence_contract["metadata_preserved"] = False
        evidence_contract["source_metadata_may_be_present_in_gateway"] = True
        evidence_contract["hosted_redaction_applied"] = True
    safe["privacy_representation"] = {
        "level": "hosted_redacted",
        "detail": detail,
        "arbitrary_metadata_disclosed": False,
        "agent_prompt_content_disclosed": False,
        "source_store_modified": False,
        "source": "stored_gateway_evidence",
    }
    return safe
