"""Content-free ChatGPT/Claude browser agent-run recall from consented evidence.

Native Claude Code/Codex agent runs are derived separately from source='agent'.
Web chat lifecycle observations have source='browser_extension' and the
distinct 'browser_agent_*' event types; do not infer a run from ordinary
navigation or an AI site being open.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

_PROVIDERS = frozenset({
    "chatgpt", "claude", "microsoft_copilot", "gemini", "lovable",
})
_ACTIONS = {
    "browser_agent_run_started": "run_started",
    "browser_agent_run_finished": "run_finished",
    "browser_agent_run_cancelled": "run_cancelled",
    "browser_agent_error": "error_observed",
    "browser_agent_approval_requested": "approval_requested",
    "browser_agent_approval_received": "approval_received",
}
_RUN = re.compile(r"^web-[A-Za-z0-9-]{8,90}$")
_RESOURCE_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")


def _at(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def browser_ai_agent_runs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return descriptive, non-authoritative AI Web lifecycle sessions.

    A stop/spinner disappearing is not a verified successful outcome. No
    observed prompt, page title or response body enters the run summary.
    """
    grouped: dict[tuple[str, str, str], list[tuple[datetime, str, dict]]] = defaultdict(list)
    # Separate resource observations have no agent_run_id. Link by the
    # *same* opaque browser tab + browser runtime + authenticated work session,
    # and by observed time. Never join on an arbitrary title or shared host.
    references: dict[tuple[str, str, str, str], list[tuple[datetime, str]]] = defaultdict(list)
    for event in events:
        if str(event.get("source") or "") != "browser_extension":
            continue
        meta = event.get("metadata")
        if not isinstance(meta, dict):
            continue
        timestamp = _at(event.get("observed_at"))
        session = str(event.get("session_id") or "")[:160]
        tab = str(meta.get("tab_id") or "")
        runtime = str(meta.get("browser_session_id") or "")
        if (event.get("event_type") == "browser_resource_reference_observed"
                and timestamp and tab and runtime and session):
            resource = meta.get("resource_reference")
            if isinstance(resource, dict):
                provider = str(resource.get("provider") or "").lower()
                token = str(resource.get("resource_ref") or "")
                if provider in _PROVIDERS and _RESOURCE_REF.fullmatch(token):
                    references[(provider, session, runtime, tab)].append((timestamp, token))
        action = _ACTIONS.get(str(event.get("event_type") or ""))
        if not action:
            continue
        provider = str(meta.get("agent_provider") or "").lower()
        run = str(meta.get("agent_run_id") or "")
        if provider not in _PROVIDERS or not _RUN.fullmatch(run):
            continue
        timestamp = _at(event.get("observed_at"))
        if timestamp is None:
            continue
        session = str(event.get("session_id") or "")[:160]
        grouped[(provider, run, session)].append((timestamp, action, event))

    summaries = []
    for (provider, run, session), rows in grouped.items():
        rows.sort(key=lambda r: (r[0], str(r[2].get("event_id") or "")))
        starts = [x for x in rows if x[1] == "run_started"]
        if not starts:
            # Never turn an isolated browser error or stale stop signal into a
            # new run that did not have an observed start.
            continue
        start = starts[0][0]
        end = rows[-1][0]
        actions = list(dict.fromkeys(action for _, action, _ in rows))
        outcome = ("cancelled" if "run_cancelled" in actions else
                   "error" if "error_observed" in actions else "unknown")
        refs: list[str] = []
        for _, _, item in rows:
            meta = item.get("metadata") or {}
            reference = meta.get("resource_reference") if isinstance(meta, dict) else None
            token = str((reference or {}).get("resource_ref") or "") if isinstance(reference, dict) else ""
            if _RESOURCE_REF.fullmatch(token) and token not in refs:
                refs.append(token)

        # Resource and lifecycle events are intentionally different captures.
        # Prefer the first reference observed up to 30s after run start:
        # new ChatGPT/Claude chats get their conversation ID after sending.
        # Only fall back to the most recent pre-run reference if none arrives.
        # Choose the earliest post-start reference to avoid later relabeling.
        first_meta = rows[0][2].get("metadata") or {}
        tab = str(first_meta.get("tab_id") or "")
        runtime = str(first_meta.get("browser_session_id") or "")
        if tab and runtime and session:
            candidates = references.get((provider, session, runtime, tab), [])
            before = [(ts, ref) for ts, ref in candidates
                      if 0 <= (start - ts).total_seconds() <= 7200]
            after = [(ts, ref) for ts, ref in candidates
                     if 0 < (ts - start).total_seconds() <= 30]
            chosen = (min(after, default=None)
                      or max(before, default=None))
            if chosen and chosen[1] not in refs:
                refs.append(chosen[1])

        # IDs are random opaque Web-session references. Include session to
        # prevent one browser run becoming another device/session's run.
        material = "|".join((provider, run, session))
        execution_id = "execution:" + hashlib.sha256(material.encode()).hexdigest()[:16]
        summaries.append({
            "execution_id": execution_id,
            "actor_kind": "agent",
            "source": "browser_ai_lifecycle",
            "provider": provider,
            "started_at": start.isoformat(),
            "ended_at": end.isoformat(),
            "duration_seconds": round(max(0.0, (end - start).total_seconds()), 3),
            "observation_level": "web_composer_lifecycle",
            "outcome_status": outcome,
            "outcome_basis": "browser_ui_signals_not_verified_business_success",
            "steps": actions[:10],
            "observed_event_count": len(rows),
            "resource_refs": refs[:5],
            "session_id": session,
            "derived": True,
            "authoritative": False,
            "needs_review": True,
            "prompt_content_observed": False,
            "response_content_observed": False,
            "task_identity_confirmed": False,
        })
    summaries.sort(key=lambda item: item["started_at"], reverse=True)
    return summaries
