"""Local-only Gateway upload projection: no raw preview/body metadata in transit.

The canonical local database is not rewritten. This projection runs after
organization/local policy evaluation but BEFORE an HTTP request is built.
Unknown metadata keys are dropped, not passed through a best-effort detector.
The Gateway retains a second (presentation) redaction layer.

This is a privacy *reduction*, not an anonymization guarantee. No detector can
promise recall on unseen people, organizations or clinical context.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import re
from typing import Any

from gateway.plugin_redaction import PublicRedactor
from server.contextual_redaction import ORGANIZATIONS, STOPWORDS

DEVICE_REDACTION_VERSION = "device-v1"
# No email bodies, messages, snippets, previews, typed inputs, clipboard or
# arbitrary strings are allowed through, regardless of their field names.
SAFE_META_STRINGS = frozenset({
    "action", "semantic_action", "semantic_action_confidence", "observation_level",
    "outcome_status", "tab_context_id", "browser_session_id", "tab_id",
    "clipboard_transfer_id", "linked_copy_event_id",
    "clipboard_source_tab_context_id", "source", "operation",
})
SAFE_META_NUMBERS = frozenset({
    "duration_seconds", "foreground_seconds", "engaged_seconds",
    "active_input_seconds", "idle_seconds", "click_count", "scroll_count",
    "keypress_count", "clipboard_link_age_seconds", "confidence",
})
SAFE_META_FLAGS = frozenset({
    "clipboard_source_observed", "clipboard_contents_captured", "excluded",
})
# Numeric-only nested instrumentation preserves timing, not content.
SAFE_NUMERIC_GROUPS = frozenset({"activity", "effort", "timing", "input"})
SAFE_PAGE_STRINGS = frozenset({"hostname", "surface"})
SAFE_TARGET_STRINGS = frozenset({"role", "tag", "localized_role", "label", "title"})
SAFE_RESOURCE_KEYS = frozenset({"resource_ref", "file_ref", "provider", "resource_kind", "resolution"})
# Raw business-object locators, URL paths and nested work text remain local.
SAFE_META_GROUPS = frozenset({"page", "target", "resource_reference", "file_reference"})
# The whole field gets dropped if it suggests patient information; a person
# token alone would not protect the special-category health disclosure.
CLINICAL_CONTEXT = re.compile(
    r"(?i)(?<!\w)(?:patient(?:en|er)?|journal(?:en|anteckning)?|"
    r"diagnos(?:en|er)?|symtom|symptom|läkarbesök|medical\s+record|"
    r"prescription|recept|personnummer|vårdkontakt|remiss)(?!\w)"
)
HANDLE = re.compile(r"(?<![\w@])@[A-Za-zÅÄÖåäö0-9_][A-Za-zÅÄÖåäö0-9_.-]{0,63}")
# High-precision social verb contexts complement (do not replace) the
# general person detector. Never interpret ordinary sentence-start verbs as
# people, and never include arbitrary remote preview text.
SOCIAL_ACTOR = re.compile(
    r"(?<!\w)([A-ZÅÄÖ][a-zåäö]{2,35})(?=\s+"
    r"(?i:kommenterade|skrev|delade|ringde|svarade|bjuder|"
    r"commented|wrote|shared|replied|called|invited)\b)"
)
SOCIAL_GREETING = re.compile(
    r"(?i:(?<!\w)(?:tack|thanks|hey|hej|välkommen|hello|welcome))"
    r"\s*[,!]?\s+([A-ZÅÄÖ][a-zåäö]{2,35})(?=[\s!,.?:;]|$)"
)
_GENERIC_GREETING = frozenset("alla all everyone everybody team world tillbaka back there oss".split())
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,159}$")


def _opaque_id(value: Any, *, secret: str, kind: str) -> str:
    raw = str(value or "")
    if SAFE_ID.fullmatch(raw):
        return raw
    digest = hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return kind + "_" + digest


class DeviceUploadRedactor:
    def __init__(self, *, secret: str):
        if not secret or len(secret) < 16:
            raise ValueError("device-scoped local redaction secret required")
        self.secret = secret
        self.cleaner = PublicRedactor(secret=secret, principal="device-gateway-upload")

    def label(self, value: Any) -> str:
        original = str(value or "")
        if not original:
            return ""
        if CLINICAL_CONTEXT.search(original):
            return "[SENSITIVE_HEALTH_CONTEXT_WITHHELD]"
        # Domain-specific safe context can be preserved around a masked handle.
        clean = self.cleaner.target_label(original)
        clean = HANDLE.sub(lambda m: self.cleaner._token("HANDLE", m.group(0)), clean)

        def actor(match: re.Match[str]) -> str:
            name = match.group(1)
            if name.casefold() in STOPWORDS or name.casefold() in ORGANIZATIONS:
                return match.group(0)
            return self.cleaner._token("PERSON", name)

        clean = SOCIAL_ACTOR.sub(actor, clean)
        clean = SOCIAL_GREETING.sub(
            lambda m: m.group(0) if m.group(1).casefold() in _GENERIC_GREETING
            else m.group(0)[:-len(m.group(1))] + self.cleaner._token("PERSON", m.group(1)),
            clean,
        )
        return clean[:240]

    def _clean_meta(self, metadata: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in SAFE_META_STRINGS:
            v = metadata.get(key)
            if isinstance(v, str) and v:
                # References are opaque; unknown values should not be treated
                # as safe just because their *key* normally holds a reference.
                if key.endswith("_id") or key in {"tab_id", "clipboard_transfer_id"}:
                    result[key] = _opaque_id(v, secret=self.secret, kind="ref")
                else:
                    result[key] = self.label(v)
        for key in SAFE_META_NUMBERS:
            v = metadata.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                result[key] = v
        for key in SAFE_META_FLAGS:
            if isinstance(metadata.get(key), bool):
                result[key] = metadata[key]
        for group in SAFE_NUMERIC_GROUPS:
            raw = metadata.get(group)
            if isinstance(raw, dict):
                safe = {k: v for k, v in raw.items()
                        if k in SAFE_META_NUMBERS and isinstance(v, (int, float))
                        and not isinstance(v, bool)}
                if safe:
                    result[group] = safe
        page = metadata.get("page")
        if isinstance(page, dict):
            safe = {k: self.label(v) for k, v in page.items()
                    if k in SAFE_PAGE_STRINGS and isinstance(v, str) and v}
            if safe:
                result["page"] = safe
        target = metadata.get("target")
        if isinstance(target, dict):
            # Preview-like values, arbitrary descriptions, accessible help text
            # and message bodies are intentionally never copied.
            safe = {k: self.label(v) for k, v in target.items()
                    if k in SAFE_TARGET_STRINGS and isinstance(v, str) and v}
            if safe:
                result["target"] = safe
        for group in ("resource_reference", "file_reference"):
            raw = metadata.get(group)
            if isinstance(raw, dict):
                safe = {k: self.label(v) if k not in {"resource_ref", "file_ref"}
                        else _opaque_id(v, secret=self.secret, kind="ref")
                        for k, v in raw.items() if k in SAFE_RESOURCE_KEYS
                        and isinstance(v, str) and v}
                if safe:
                    result[group] = safe
        # Preserve the specifically allowlisted agent execution graph.
        # This channel is separately opt-in, and still never includes prompts,
        # response bodies, tool arguments/results or arbitrary nested fields.
        if metadata.get("actor_kind") == "agent" or metadata.get("source") == "agent":
            result["actor_kind"] = "agent"
            agent = metadata.get("agent")
            if isinstance(agent, dict):
                safe = {k: self.label(v) for k, v in agent.items()
                        if k in {"name", "provider", "framework", "model"}
                        and isinstance(v, str) and v}
                if safe:
                    result["agent"] = safe
            trace = metadata.get("trace")
            if isinstance(trace, dict):
                safe = {k: _opaque_id(v, secret=self.secret, kind="ref")
                        for k, v in trace.items()
                        if k in {"run_id", "trace_id", "span_id", "parent_span_id",
                                 "workflow_id", "trigger_event_id"}
                        and isinstance(v, str) and v}
                if safe:
                    result["trace"] = safe
            tool = metadata.get("tool")
            if isinstance(tool, dict):
                safe = {k: self.label(v) for k, v in tool.items()
                        if k in {"name", "category"} and isinstance(v, str) and v}
                if safe:
                    result["tool"] = safe
            usage = metadata.get("usage")
            if isinstance(usage, dict):
                safe = {k: v for k, v in usage.items()
                        if k in {"input_tokens", "output_tokens", "cached_input_tokens",
                                 "total_tokens"} and isinstance(v, int) and not isinstance(v, bool)}
                if safe:
                    result["usage"] = safe
            workspace = metadata.get("workspace_ref")
            if isinstance(workspace, str) and workspace:
                result["workspace_ref"] = _opaque_id(workspace, secret=self.secret, kind="ref")
        result["gateway_sync"] = {
            "source": "device_local_projection",
            "device_redaction_version": DEVICE_REDACTION_VERSION,
            "raw_metadata_preserved": False,
            "preview_text_dropped": True,
        }
        return result

    def event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise ValueError("invalid event")
        # Preserve canonical structural ordering/IDs, never original metadata
        # blobs. This is a projection, not a mutation of the local evidence DB.
        original = copy.deepcopy(event)
        result = {k: original[k] for k in (
            "event_id", "observed_at", "schema_version", "source", "session_id",
            "event_type", "duration_seconds", "sensor_id", "device_id",
        ) if k in original}
        for key in ("event_id", "session_id", "sensor_id", "device_id"):
            if key in result and isinstance(result[key], str):
                result[key] = _opaque_id(result[key], secret=self.secret, kind=key)
        result["app"] = self.label(original.get("app"))
        result["window_title"] = self.label(original.get("window_title"))
        raw_meta = original.get("metadata")
        result["metadata"] = self._clean_meta(raw_meta if isinstance(raw_meta, dict) else {})
        return result


def project_event_for_upload(event: dict[str, Any], *, secret: str) -> dict[str, Any]:
    """Strict pre-HTTP projection, with no fallback to original event on error."""
    return DeviceUploadRedactor(secret=secret).event(event)
