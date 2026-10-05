from __future__ import annotations

"""Conservative resource + agent continuity context for connected AI.

This module never decides what a task "is". It projects already-authorized AI
context into stable resource nodes, explicit evidence-backed relations, and
bounded candidate sets. Temporal proximity is exposed as ambiguity, not proof.
"""

from collections import defaultdict
from datetime import datetime, timedelta
import re
from typing import Any


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}")
_REDACTED_TOKEN_RE = re.compile(r"^(?:PERSON|EMAIL|PHONE|PERSONNUMMER|ID)_[0-9A-F]{4,}$", re.I)
_STOP_TOKENS = {
    "gmail", "google", "chrome", "safari", "firefox", "edge", "browser",
    "salesforce", "notion", "slack", "teams", "microsoft", "outlook",
    "excel", "word", "powerpoint", "drive", "sheet", "sheets", "document",
    "documents", "window", "page", "home", "inbox", "draft", "drafts",
    "github", "jira", "linear", "claude", "chatgpt", "codex", "cursor",
    "renewal", "pricing", "price", "customer", "account", "contract", "quote",
    "invoice", "issue", "ticket", "project", "review", "update", "request",
    "order", "report", "task", "case",
}
_MAX_HINT = 240


def _bounded(value: Any, limit: int = _MAX_HINT) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except Exception:
        return None


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata")
    return value if isinstance(value, dict) else {}


def extract_resource_pointers(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only stable, bounded resource identity from one authorized row.

    Deliberately omitted: local file paths, file hashes, file sizes, message
    contents, arbitrary metadata and full URLs. A resolver locator is retained
    only if it is already present in the authorized input (for example after the
    existing Full-detail local resolution step).
    """
    meta = _metadata(item)
    pointers: list[dict[str, Any]] = []

    reference = meta.get("resource_reference")
    if isinstance(reference, dict):
        token = str(reference.get("resource_ref") or "").strip()
        if token.startswith("owg:r:"):
            pointer = {
                "provider": _bounded(reference.get("provider"), 64),
                "resource_kind": _bounded(reference.get("resource_kind"), 64),
                "host": _bounded(reference.get("host"), 253),
                "resource_ref": token,
                "resolution": _bounded(reference.get("resolution") or "observed", 48),
            }
            locator = _bounded(reference.get("resolver_locator"), 320)
            if locator:
                pointer["resolver_locator"] = locator
            pointers.append({k: v for k, v in pointer.items() if v})

    file_reference = meta.get("file_reference")
    if isinstance(file_reference, dict):
        token = str(file_reference.get("file_ref") or "").strip()
        if token.startswith("owg:f:"):
            pointers.append({
                "provider": "local_file",
                "resource_kind": "file",
                "resource_ref": token,
                "resolution": "observed",
            })

    # Keep order deterministic while de-duplicating malformed duplicate metadata.
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for pointer in pointers:
        key = str(pointer.get("resource_ref") or "")
        if key and key not in seen:
            seen.add(key)
            output.append(pointer)
    return output


def _hint_tokens(item: dict[str, Any]) -> set[str]:
    values = [
        item.get("window_title"),
        item.get("target_label"),
        item.get("work_surface"),
    ]
    tokens: set[str] = set()
    for value in values:
        for raw in _TOKEN_RE.findall(str(value or "")):
            token = raw.casefold()
            if token in _STOP_TOKENS:
                continue
            if len(token) < 4 and not _REDACTED_TOKEN_RE.fullmatch(raw):
                continue
            if token.isdigit():
                continue
            tokens.add(token)
            if len(tokens) >= 24:
                return tokens
    return tokens


def _resource_rows(recent_evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    nonresolvable_messages = 0
    for item in recent_evidence:
        if not isinstance(item, dict):
            continue
        meta = _metadata(item)
        if isinstance(meta.get("message_reference"), dict):
            # Current Outlook observation can identify a selected message by
            # subject/time but cannot resolve it through a stable provider ID.
            nonresolvable_messages += 1
        pointers = extract_resource_pointers(item)
        if not pointers:
            continue
        observed = _ts(item.get("observed_at"))
        if observed is None:
            continue
        try:
            duration = max(0.0, float(item.get("duration_seconds") or 0))
        except Exception:
            duration = 0.0
        for pointer in pointers:
            rows.append({
                "pointer": pointer,
                "event_id": str(item.get("event_id") or ""),
                "observed_at": observed,
                "ended_at": observed + timedelta(seconds=duration),
                "app": _bounded(item.get("app"), 120),
                "display_hint": _bounded(item.get("window_title") or item.get("target_label"), _MAX_HINT),
                "tokens": _hint_tokens(item),
                "tab_context_id": _bounded(item.get("tab_context_id"), 160),
            })
    rows.sort(key=lambda row: row["observed_at"])
    return rows, nonresolvable_messages


def _summarize_resources(rows: list[dict[str, Any]], max_resources: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        pointer = row["pointer"]
        ref = str(pointer.get("resource_ref") or "")
        if not ref:
            continue
        bucket = grouped.setdefault(ref, {
            **pointer,
            "first_seen": row["observed_at"],
            "last_seen": row["observed_at"],
            "observation_count": 0,
            "evidence_event_ids": [],
            "apps": [],
            "display_hint": "",
            "_tokens": set(),
            "_intervals": [],
        })
        bucket["first_seen"] = min(bucket["first_seen"], row["observed_at"])
        bucket["last_seen"] = max(bucket["last_seen"], row["observed_at"])
        bucket["observation_count"] += 1
        if row["event_id"] and row["event_id"] not in bucket["evidence_event_ids"]:
            bucket["evidence_event_ids"].append(row["event_id"])
        if row["app"] and row["app"] not in bucket["apps"]:
            bucket["apps"].append(row["app"])
        if row["display_hint"]:
            bucket["display_hint"] = row["display_hint"]
        bucket["_tokens"].update(row["tokens"])
        bucket["_intervals"].append((row["observed_at"], row["ended_at"]))

    resources = sorted(grouped.values(), key=lambda item: item["last_seen"], reverse=True)[:max_resources]
    output: list[dict[str, Any]] = []
    for item in resources:
        output.append({
            **{k: v for k, v in item.items() if not k.startswith("_")},
            "first_seen": item["first_seen"].isoformat(),
            "last_seen": item["last_seen"].isoformat(),
            "evidence_event_ids": item["evidence_event_ids"][-12:],
            "apps": item["apps"][:6],
        })
    return output


def _resource_internal(rows: list[dict[str, Any]], refs: set[str]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        ref = str(row["pointer"].get("resource_ref") or "")
        if ref not in refs:
            continue
        bucket = grouped.setdefault(ref, {"tokens": set(), "intervals": [], "events": [], "tabs": set()})
        bucket["tokens"].update(row["tokens"])
        bucket["intervals"].append((row["observed_at"], row["ended_at"]))
        if row["event_id"]:
            bucket["events"].append(row["event_id"])
        if row["tab_context_id"]:
            bucket["tabs"].add(row["tab_context_id"])
    return grouped


def _gap_seconds(left: tuple[datetime, datetime], right: tuple[datetime, datetime]) -> float:
    if left[1] < right[0]:
        return (right[0] - left[1]).total_seconds()
    if right[1] < left[0]:
        return (left[0] - right[1]).total_seconds()
    return 0.0


def _resource_relations(resources: list[dict[str, Any]], internal: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    refs = [str(item.get("resource_ref") or "") for item in resources]
    for index, left_ref in enumerate(refs):
        left = internal.get(left_ref) or {}
        for right_ref in refs[index + 1:]:
            right = internal.get(right_ref) or {}
            left_intervals = left.get("intervals") or []
            right_intervals = right.get("intervals") or []
            if not left_intervals or not right_intervals:
                continue
            gap = min(_gap_seconds(a, b) for a in left_intervals for b in right_intervals)
            shared_tabs = sorted((left.get("tabs") or set()) & (right.get("tabs") or set()))
            shared_terms = sorted((left.get("tokens") or set()) & (right.get("tokens") or set()))

            if shared_tabs and shared_terms and gap <= 1800:
                kind, score, status, merge = "same_tab_and_shared_context", 0.88, "possible", True
            elif shared_terms and gap <= 900:
                kind, score, status, merge = "shared_context_hint", 0.75, "possible", True
            elif shared_tabs and gap <= 300:
                # Tabs are routinely reused for unrelated work. Keep this edge
                # visible for the model, but never merge candidates from it alone.
                kind, score, status, merge = "same_tab_context", 0.50, "ambiguous", False
            elif gap <= 120:
                kind, score, status, merge = "temporal_proximity", 0.35, "ambiguous", False
            else:
                continue

            relation = {
                "left_resource_ref": left_ref,
                "right_resource_ref": right_ref,
                "relation": kind,
                "confidence": score,
                "association_status": status,
                "gap_seconds": round(max(0.0, gap), 1),
                "evidence_event_ids": list(dict.fromkeys((left.get("events") or [])[-3:] + (right.get("events") or [])[-3:])),
                "proves_same_work": False,
                "_merge_candidate": merge,
            }
            if shared_terms:
                relation["shared_context_terms"] = shared_terms[:4]
            relations.append(relation)
    relations.sort(key=lambda item: (-float(item["confidence"]), float(item["gap_seconds"])))
    return relations


def _agent_summary(execution: dict[str, Any]) -> dict[str, Any] | None:
    execution_id = str(execution.get("execution_id") or "")
    if not execution_id.startswith("execution:"):
        return None
    out = {
        "execution_id": execution_id,
        "started_at": execution.get("started_at"),
        "ended_at": execution.get("ended_at"),
        "agent": execution.get("agent"),
        "workspace_ref": execution.get("workspace_ref"),
        "outcome_status": execution.get("outcome_status"),
        "complete_boundary_observed": execution.get("complete_boundary_observed"),
        "observation_level": execution.get("observation_level"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _agent_relations(
    resources: list[dict[str, Any]],
    internal: dict[str, dict[str, Any]],
    agent_executions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    agents = [summary for raw in agent_executions if isinstance(raw, dict) if (summary := _agent_summary(raw))]
    agents = agents[:12]
    relations: list[dict[str, Any]] = []
    for agent in agents:
        start = _ts(agent.get("started_at"))
        end = _ts(agent.get("ended_at")) or start
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        for resource in resources:
            ref = str(resource.get("resource_ref") or "")
            intervals = (internal.get(ref) or {}).get("intervals") or []
            if not intervals:
                continue
            gap = min(_gap_seconds(interval, (start, end)) for interval in intervals)
            if gap == 0:
                score, status, kind = 0.60, "possible", "temporal_overlap"
            elif gap <= 300:
                score, status, kind = 0.45, "ambiguous", "temporal_proximity"
            else:
                continue
            relations.append({
                "resource_ref": ref,
                "execution_id": agent["execution_id"],
                "relation": kind,
                "confidence": score,
                "association_status": status,
                "gap_seconds": round(max(0.0, gap), 1),
                "proves_same_work": False,
            })
    relations.sort(key=lambda item: (-float(item["confidence"]), float(item["gap_seconds"])))
    return agents, relations


def _candidate_sets(
    resources: list[dict[str, Any]],
    resource_relations: list[dict[str, Any]],
    agent_relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return non-transitive pair candidates plus unpaired singleton resources.

    A-B and B-C evidence must never silently become an A-B-C task. The external
    AI can inspect the graph and source objects; OWG keeps each asserted
    association tied to the exact pair of resources that supports it.
    """
    refs = [str(item.get("resource_ref") or "") for item in resources if item.get("resource_ref")]
    paired_refs: set[str] = set()
    groups: list[tuple[list[str], dict[str, Any] | None]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for relation in resource_relations:
        if not relation.get("_merge_candidate"):
            continue
        left = str(relation.get("left_resource_ref") or "")
        right = str(relation.get("right_resource_ref") or "")
        if left not in refs or right not in refs or left == right:
            continue
        pair = tuple(sorted((left, right)))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        paired_refs.update(pair)
        groups.append(([left, right], relation))

    for ref in refs:
        if ref not in paired_refs:
            groups.append(([ref], None))

    candidates: list[dict[str, Any]] = []
    for idx, (group, relation) in enumerate(groups[:12], start=1):
        attached_agents = sorted({
            str(edge.get("execution_id"))
            for edge in agent_relations
            if str(edge.get("resource_ref")) in group and float(edge.get("confidence") or 0) >= 0.45
        })
        candidate: dict[str, Any] = {
            "candidate_id": f"continuity_candidate_{idx:02d}",
            "resource_refs": group,
            "agent_execution_ids": attached_agents,
            "task_label": None,
            "task_identity_inferred": False,
            "association_status": (
                str(relation.get("association_status") or "possible")
                if relation is not None else ("possible" if attached_agents else "observed_resource")
            ),
            "proves_same_work": False,
        }
        if relation is not None:
            candidate["association_confidence"] = round(float(relation.get("confidence") or 0), 2)
            candidate["association_evidence"] = [{
                key: value for key, value in relation.items()
                if not key.startswith("_") and key in {
                    "left_resource_ref", "right_resource_ref", "relation", "confidence",
                    "association_status", "gap_seconds", "shared_context_terms", "evidence_event_ids",
                }
            }]
        candidates.append(candidate)
    return candidates


def build_continuity_context(
    recent_evidence: list[dict[str, Any]],
    agent_executions: list[dict[str, Any]] | None = None,
    *,
    max_resources: int = 12,
) -> dict[str, Any]:
    """Build a bounded continuity graph from already-authorized AI context."""
    max_resources = max(1, min(int(max_resources), 24))
    evidence = [item for item in recent_evidence if isinstance(item, dict)]
    rows, nonresolvable_messages = _resource_rows(evidence)
    resources = _summarize_resources(rows, max_resources)
    refs = {str(item.get("resource_ref") or "") for item in resources}
    internal = _resource_internal(rows, refs)
    resource_relations = _resource_relations(resources, internal)
    agents, agent_relations = _agent_relations(resources, internal, list(agent_executions or []))
    candidates = _candidate_sets(resources, resource_relations, agent_relations)

    observed_items: list[tuple[datetime, dict[str, Any]]] = []
    for resource in resources:
        when = _ts(resource.get("last_seen"))
        if when:
            observed_items.append((when, {"kind": "resource", "resource_ref": resource.get("resource_ref"), "observed_at": resource.get("last_seen")}))
    for agent in agents:
        when = _ts(agent.get("ended_at") or agent.get("started_at"))
        if when:
            observed_items.append((when, {"kind": "agent_run", "execution_id": agent.get("execution_id"), "observed_at": (agent.get("ended_at") or agent.get("started_at"))}))
    last_observed = max(observed_items, key=lambda item: item[0])[1] if observed_items else None

    return {
        "resources": resources,
        "agent_runs": agents,
        "resource_relations": [{k: v for k, v in r.items() if not k.startswith("_")} for r in resource_relations[:24]],
        "resource_agent_relations": agent_relations[:24],
        "candidates": candidates,
        "last_observed": last_observed,
        "unfinished_state": {
            "status": "unknown",
            "reason": "OpenWorkGraph observes activity and boundaries; it does not infer task completion from silence or recency.",
        },
        "coverage": {
            "recent_evidence_rows_considered": len(evidence),
            "resource_occurrences": len(rows),
            "distinct_stable_resources": len(resources),
            "agent_runs_considered": len(agents),
            "nonresolvable_message_references_observed": nonresolvable_messages,
        },
        "association_contract": {
            "stable_pointer_identity_is_observed": True,
            "same_task_identity_is_observed": False,
            "temporal_proximity_proves_same_work": False,
            "candidate_sets_are_derived": True,
            "candidate_associations_are_pairwise_nontransitive": True,
            "task_names_are_not_generated": True,
            "ask_when_ambiguous": True,
            "recommended_next_step": "Use authorized source-system connectors to inspect candidate resources before acting.",
        },
        "authoritative": False,
        "derived": True,
        "purpose": "cross_app_and_cross_agent_work_continuity",
    }


__all__ = ["build_continuity_context", "extract_resource_pointers"]
