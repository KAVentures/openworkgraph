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
from typing import Any, Iterable

from server.contextual_redaction import Detector, Settings, STOPWORDS, ORGANIZATIONS
from shared.core.identity_cues import redact_explicit_person_cues, redact_labeled_person_fields

_EMAIL = re.compile(r"(?<![\w.])([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})(?![\w.])")
_SWEDISH_ID = re.compile(r"\b(?:\d{8}[-+]?\d{4}|\d{6}[-+]\d{4})\b")
_PHONE = re.compile(r"(?<![\w])(?:\+\d{1,3}[\s.()-]*)?(?:\d[\s.()-]*){9,15}(?![\w])")
_CREDENTIAL = re.compile(r"\b(?:Bearer\s+[A-Za-z0-9._~-]{10,}|sk-[A-Za-z0-9_-]{18,}|(?:sk|ghp|gho|github_pat|glpat)_[A-Za-z0-9_-]{15,})", re.I)
_URL = re.compile(r"https?://[^\s<>]+", re.I)
_META_SIMPLE = frozenset({
    "action", "semantic_action", "semantic_action_confidence",
    "tab_id", "tab_context_id", "browser_session_id",
    "duration_seconds", "click_count", "scroll_count", "keypress_count",
    "foreground_seconds", "engaged_seconds", "active_input_seconds",
    "idle_seconds", "observation_level", "outcome_status",
    "clipboard_transfer_id", "linked_copy_event_id", "clipboard_source_tab_context_id",
    "clipboard_link_age_seconds", "clipboard_source_observed", "clipboard_contents_captured",
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
    "clipboard_transfer_id", "linked_copy_event_id", "clipboard_source_tab_context_id",
    "page_ref",
})
_ID_LISTS = frozenset({"evidence_event_ids", "event_ids", "ordered_event_ids",
                       "supporting_execution_ids", "execution_ids", "resource_refs"})
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,159}$")
_MAX_TEXT = 240

_COMPANY_NAME = re.compile(
    r"(?<![\w])(?:[A-ZÅÄÖ][\wÅÄÖåäö&.-]{1,45}\s+){1,4}"
    r"(?:AB|Aktiebolag|Ltd|LLC|Inc|GmbH|Oy|PLC)\b"
)
# Only genuinely ambiguous single-word labels need a conservative fallback.
# Multi-word interface text is NOT checked against an English vocabulary list.
_SAFE_SINGLE_UI = frozenset("""
open close save cancel continue retry settings next previous refresh
new edit view search submit copy paste share help download upload
openworkgraph owg github gmail chatgpt claude oneflow
välkommen ring jobbar arbetar svar uppdatering hälsa
ny hämta spara sök avbryt fortsätt tillbaka inställningar rapportering
""".split())
_SINGLE_CAPTION = re.compile(r"^[A-ZÅÄÖ][A-Za-zÅÄÖåäö-]{2,49}$")
# High-precision cues for uncommon single names absent from the lexicon.
# Make only the *cue* case insensitive. The name itself must start with an
# uppercase letter: otherwise Swedish "till oss på..." can redact "oss".
_CONTACT_CUE = re.compile(
    r"(?i:(\b(?:for|from|to|with|contact|attn|för|från|till|med|private)\s+))"
    r"([A-ZÅÄÖ][a-zåäö]{2,35})"
    r"(?=\s+(?i:at|hos|på|from|från|till|to|med|with|i|message|email)\b|[,;:–—-]|$)"
)
_LEADING_CONTACT = re.compile(
    r"^([A-ZÅÄÖ][a-zåäö]{2,35})(?=\s+(?i:at|hos|på|from|från|to|med|with)\b)"
)
# Explicit grammatical identity and closing-signature cues cover unfamiliar
# names missing from the first-name dictionary, including Swedish email bodies.
_SELF_NAME_CUE = re.compile(
    r"(?i:(?<!\w)(?:mitt\s+namn\s+är|jag\s+heter|namnet\s+är|"
    r"my\s+name\s+is|vänliga\s+hälsningar|med\s+vänliga\s+hälsningar|"
    r"hälsningar|best\s+regards|kind\s+regards|regards))"
    r"\s*[,:\-]?\s*([A-ZÅÄÖ][a-zåäö]{2,40})\b"
)
_SLASH_SIGNATURE = re.compile(
    r"(?<![\w/])/[ \t]+([A-ZÅÄÖ][a-zåäö]{2,40})(?=\s*(?:$|[\n.,;]))"
)
_EMPLOYER_CUE = re.compile(
    r"(?i:(?<!\w)(?:jag\s+jobbar|jag\s+arbetar|jobbar|arbetar|"
    r"anställd|employed|i\s+work|work)\s+(?:på|hos|at|for)\s+)"
    r"([A-ZÅÄÖ][A-Za-zÅÄÖåäö\-]{3,49})\b"
)
_PSEUDONYM = re.compile(r"^(?:OWNER|EMAIL|PERSON|ORG|TARGET|STEP|QUERY|ID|PHONE|SECRET)_[A-Z0-9]{8,24}$")
_BARE_OWNER = re.compile(r"(?<![\w])OWNER(?![\w])")

# Curated organizations are loaded from the *shared* contextual entity lexicon.
# The generic person detector is not permitted to relabel a known organization.
_KNOWN_ORG_BRANDS = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(name)
        for name in sorted(ORGANIZATIONS, key=lambda word: (-len(word), word))) + r")(?!\w)",
    re.I,
)
# Treat common greetings as contexts for PERSON spans, even if the greeting is
# protected as a safe UI word. This never classifies the greeting itself.
_GREETING_NAME_CUE = re.compile(
    r"(?i:(?<!\w)(?:välkommen|hej|hejsan|hello|hi|dear|welcome))"
    r"[ \t]*[,!]?\s+([A-ZÅÄÖ][a-zåäö]{2,40})(?=[\s!,.?:;]|$)"
)
_GREETING_GENERIC = frozenset(
    "alla all back everyone everybody team world there kollegor vänner tillbaka".split()
)

class PublicRedactor:
    def __init__(self, *, secret: str, principal: str, owner_names: Iterable[str] = ()):
        if not secret or not principal:
            raise ValueError("public redaction requires a scoped secret")
        self.key = hmac.new(secret.encode(), principal.encode(), hashlib.sha256).digest()
        # These are unambiguous UI verbs/greetings in this connector's
        # telemetry. Protect just these spans from lexicon false positives,
        # while continuing to redact adjacent names and all other PII.
        self.person_detector = Detector(
            token=self._token,
            settings=Settings(never_redact=("Välkommen", "Ring", "Jobbar", "Arbetar", "Svar", "Uppdatering", "Hälsa")),
        )
        # Only aliases proven by organization suffixes in this one redaction
        # request. Never persist personal/customer names across accounts.
        self.organization_aliases: set[str] = set()
        # Authenticated identity hints redact owner names in all text surfaces.
        names = {str(name).strip() for name in owner_names
                 if isinstance(name, str) and 3 <= len(name.strip()) <= 100}
        self.owner_token = self._token("OWNER", principal)
        self.owner_patterns = [
            re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])", re.I)
            for name in sorted(names, key=lambda name: (-len(name), name.casefold()))
        ]

    def _token(self, kind: str, value: str) -> str:
        digest = hmac.new(self.key, value.casefold().strip().encode(), hashlib.sha256).hexdigest()[:10].upper()
        return f"{kind}_{digest}"

    def text(self, value: str, *, max_chars: int = _MAX_TEXT) -> str:
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
        def company(match: re.Match[str]) -> str:
            phrase = match.group(0)
            first = phrase.split()[0]
            if len(first) >= 5 and first.casefold() not in STOPWORDS:
                self.organization_aliases.add(first)
            return self._token("ORG", phrase)

        text = _COMPANY_NAME.sub(company, text)
        text = _KNOWN_ORG_BRANDS.sub(lambda m: self._token("ORG", m.group(0)), text)
        # Strong employment context also identifies brands without AB/Ltd.
        text = _EMPLOYER_CUE.sub(
            lambda m: m.group(0)[:-len(m.group(1))] + self._token("ORG", m.group(1)),
            text,
        )
        for company_alias in sorted(self.organization_aliases, key=len, reverse=True):
            text = re.sub(
                r"(?<!\w)" + re.escape(company_alias) + r"(?!\w)",
                lambda m: self._token("ORG", m.group(0)), text,
                flags=re.IGNORECASE,
            )
        text = _BARE_OWNER.sub(self.owner_token, text)
        # The same token must win for full owner name and first-name suffix,
        # even if the generic detector would otherwise use a PERSON token.
        for pattern in self.owner_patterns:
            text = pattern.sub(self.owner_token, text)
        text = redact_labeled_person_fields(
            text, person_token=lambda name: self._token("PERSON", name),
            blocked_names=STOPWORDS | ORGANIZATIONS,
        )
        text = self.person_detector.redact(text)
        # The high-precision identity/signature cues are shared with local
        # at-rest redaction. Hosted tokens keep their existing account-scoped key.
        text = redact_explicit_person_cues(
            text, person_token=lambda name: self._token("PERSON", name),
            blocked_names=_SAFE_SINGLE_UI | STOPWORDS | ORGANIZATIONS,
        )
        max_chars = max(40, min(int(max_chars), 1200))
        return text[:max_chars] + "…[truncated]" if len(text) > max_chars else text

    def target_label(self, value: str, *, token_kind: str = "TARGET") -> str:
        """Mask sensitive spans, retaining Swedish UI, PR numbers and resources.

        Redaction is best-effort: unknown names *without* contextual cues are
        inherently ambiguous. Single-word name-shaped labels use a conservative
        fallback, but normal multiword UI text is never tested against a fixed
        English vocabulary. Original permitted evidence remains unchanged.
        """
        safe = self.text(str(value), max_chars=1200)
        if _PSEUDONYM.fullmatch(safe):
            return safe

        def contact(match: re.Match[str]) -> str:
            caption = match.group(2)
            # Do not mask clearly generic UI terms after a cue.
            if caption.casefold() in (_SAFE_SINGLE_UI | STOPWORDS):
                return match.group(0)
            return match.group(1) + self._token("PERSON", caption)

        safe = _CONTACT_CUE.sub(contact, safe)
        safe = _LEADING_CONTACT.sub(
            lambda m: self._token("PERSON", m.group(1))
            if m.group(1).casefold() not in (_SAFE_SINGLE_UI | STOPWORDS) else m.group(1), safe)
        if (_SINGLE_CAPTION.fullmatch(safe)
                and safe.casefold() not in (_SAFE_SINGLE_UI | STOPWORDS)):
            return self._token(token_kind, safe)
        return safe[:_MAX_TEXT] + "…[truncated]" if len(safe) > _MAX_TEXT else safe

    def metadata(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        output: dict[str, Any] = {}
        for key in _META_SIMPLE:
            value = raw.get(key)
            if isinstance(value, (bool, int, float)):
                if isinstance(value, bool) or value not in (0, 0.0):
                    output[key] = value
            elif isinstance(value, str) and value and key not in _DROP_FIELDS:
                if key in _IDS:
                    if _OPAQUE.fullmatch(value):
                        output[key] = value
                else:
                    output[key] = self.text(value)
        for key, allowed in (("target", _META_TARGET), ("page", _META_PAGE),
                             ("resource_reference", _META_REFERENCE),
                             ("file_reference", _META_REFERENCE)):
            child = raw.get(key)
            if not isinstance(child, dict):
                continue
            safe = {k: (v if k in _IDS else self.target_label(v)
                         if key == "target" and k in {"label", "title"}
                         else self.target_label(v) if key == "target" else self.text(v)) if isinstance(v, str) else v
                    for k, v in child.items() if k in allowed
                    and isinstance(v, (str, int, float, bool)) and v not in (None, "", 0, False)
                    and (k not in _IDS or isinstance(v, str) and _OPAQUE.fullmatch(v))}
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
                else self.target_label(str(value)) if key == "target_label"
                else self.target_label(str(value), token_kind="TITLE") if key == "window_title"
                else self.text(str(value))
            )
        meta = self.metadata(raw.get("metadata"))
        if isinstance(raw.get("matched_fields"), list):
            result["matched_fields"] = [key for key in raw["matched_fields"]
                if key in {"app", "window_title", "target_label", "page_host", "semantic_action", "resource_ref"}]
        if detail == "rich":
            if meta:
                result["metadata"] = meta
        else:
            # Pull just the structural anchor; no duplicate page/target labels
            # or arbitrary nested content in the default response.
            reference = meta.get("resource_reference")
            if isinstance(reference, dict) and reference.get("resource_ref"):
                result["resource_ref"] = reference["resource_ref"]
        # Cross-app linkage is structural evidence, useful at either detail level.
        for key in ("clipboard_transfer_id", "linked_copy_event_id", "clipboard_source_tab_context_id",
                    "clipboard_link_age_seconds", "clipboard_source_observed", "clipboard_contents_captured"):
            if key in meta:
                result[key] = meta[key]
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
                if safe not in (None, "", [], {}) or name in {
                    "onboarding", "rows", "next_cursor", "resources",
                    "agent_runs", "resource_relations", "resource_agent_relations",
                    "candidates", "executions", "canonical_evidence", "examples",
                    "latest_observed_at", "recent_evidence_latest_received_at", "latest_snapshot_at",
                }:
                    # Public MCP compatibility: an empty array / null cursor
                    # is meaningful and must never disappear on projection.
                    result[name] = safe
            return result
        if isinstance(value, list):
            return [self.visit(x, detail=detail, key=key) for x in value]
        if isinstance(value, tuple):
            return [self.visit(x, detail=detail, key=key) for x in value]
        if isinstance(value, str):
            if key in _ID_LISTS:
                return value if _OPAQUE.fullmatch(value) else "[INVALID_REFERENCE]"
            if key == "target_label":
                return self.target_label(value)
            if key == "query":
                return self.target_label(value, token_kind="QUERY")
            if key in {"window_title", "page_title"}:
                return self.target_label(value, token_kind="TITLE")
            if key in {"step", "readable_steps", "semantic_steps", "core_steps",
                       "readable_step", "from_step", "to_step"}:
                if value.startswith(("surface:", "action:", "tool:")) and _OPAQUE.fullmatch(value):
                    return value
                return self.target_label(value, token_kind="STEP")
            return value if key in _IDS or key in {"observed_at", "snapshot_until", "next_cursor"} else self.text(value)
        return value


def project(value: dict[str, Any], *, secret: str, principal: str, detail: str = "compact", owner_names: Iterable[str] = ()) -> dict[str, Any]:
    if detail not in {"compact", "rich"}:
        raise ValueError("detail must be compact or rich")
    safe = PublicRedactor(secret=secret, principal=principal, owner_names=owner_names).visit(value, detail=detail)
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
