from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "cases.jsonl"

_APP = {
    "gmail": ("Google Chrome", "Gmail", "mail.google.com"),
    "salesforce": ("Google Chrome", "Salesforce", "acme.salesforce.com"),
    "sheets": ("Google Chrome", "Google Sheets", "docs.google.com"),
    "slack": ("Slack", "Slack", ""),
    "jira": ("Google Chrome", "Jira", "acme.atlassian.net"),
    "outlook": ("Microsoft Outlook", "Outlook", ""),
    "excel": ("Microsoft Excel", "Microsoft Excel", ""),
    "pricing": ("Internal Pricing Tool", "Internal Pricing Tool", ""),
    "github": ("Google Chrome", "GitHub", "github.com"),
    "docs": ("Google Chrome", "Google Docs", "docs.google.com"),
    "linear": ("Google Chrome", "Linear", "linear.app"),
    "terminal": ("Terminal", "Terminal", ""),
}

_KIND = {
    "gmail": ("gmail", "thread_locator"),
    "salesforce": ("salesforce", "record"),
    "sheets": ("google_drive", "spreadsheet"),
    "jira": ("jira", "issue"),
    "github": ("github", "pull_request"),
    "docs": ("google_drive", "document"),
    "linear": ("linear", "issue"),
}

def _iso(base: datetime, seconds: int) -> str:
    return (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

def _event(
    idx: int,
    seconds: int,
    surface: str,
    action: str,
    *,
    label: str,
    resource: str | None,
    workflow: str,
    checkpoint: str | None = None,
    role: str = "checkpoint",
    tab: str | None = None,
    clipboard_transfer: str | None = None,
    clipboard_action: str | None = None,
    omit_resource: bool = False,
    actor_kind: str = "human",
) -> dict[str, Any]:
    base = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)
    app, title, host = _APP[surface]
    event_id = f"e{idx:03d}"
    metadata: dict[str, Any] = {
        "action": action,
        "semantic_action": action,
        "semantic_action_confidence": "high",
        "target": {"role": "button", "label": label},
    }
    if host:
        metadata["page"] = {
            "hostname": host,
            "pathname": f"/opaque/{resource or surface}",
            "title": f"{title} · {label}",
        }
    if tab:
        metadata["tab_context_id"] = tab
    if resource and surface in _KIND and not omit_resource:
        provider, kind = _KIND[surface]
        metadata["resource_reference"] = {
            "provider": provider,
            "resource_kind": kind,
            "resource_ref": f"owg:r:{resource}",
        }
    if clipboard_transfer:
        metadata["clipboard_transfer_id"] = clipboard_transfer
        metadata["clipboard_source_observed"] = clipboard_action == "paste"
        metadata["action"] = clipboard_action or action
    return {
        "event_id": event_id,
        "observed_at": _iso(base, seconds),
        "schema_version": "1.0",
        "organization_id": "local",
        "actor_id": "human" if actor_kind == "human" else "agent:synthetic",
        "device_id": "device:test",
        "sensor_id": "synthetic:reconstruction",
        "source": "browser_extension" if host else "desktop_sensor",
        "session_id": "session:synthetic-workday",
        "app": app,
        "window_title": f"{title} · {label}",
        "event_type": f"browser_{clipboard_action or action}" if host else ("focus_span" if action == "focus" else f"screen_{action}"),
        "duration_seconds": 8 if action == "focus" else 0,
        "metadata": metadata,
        "_truth": {
            "workflow": workflow,
            "checkpoint": checkpoint,
            "role": role,
        },
    }

def _source_event(event: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(event)
    out.pop("_truth", None)
    return out

def _trace_projection(event: dict[str, Any]) -> dict[str, Any]:
    """Mirror shared.evidence.rich_evidence_row(..., include_identity=False)."""
    meta = deepcopy(event.get("metadata") or {})
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
    target = meta.get("target") if isinstance(meta.get("target"), dict) else {}
    target_label = (
        target.get("label")
        or target.get("title")
        or target.get("description")
        or target.get("help")
        or ""
    )
    target_role = (
        target.get("role")
        or target.get("localized_role")
        or target.get("tag")
        or ""
    )
    out: dict[str, Any] = {
        "event_id": event.get("event_id"),
        "observed_at": event.get("observed_at"),
        "app": event.get("app"),
        "window_title": event.get("window_title"),
        "event_type": event.get("event_type"),
        "duration_seconds": event.get("duration_seconds", 0) or 0,
        "source": event.get("source", ""),
        "session_id": event.get("session_id", ""),
        "metadata": meta,
        "has_local_screenshot": False,
        "action": meta.get("action", ""),
        "target_label": target_label,
        "target_role": target_role,
        "page_host": page.get("hostname", ""),
        "page_path": page.get("pathname", ""),
        "browser_hostname": page.get("hostname", ""),
        "tab_context_id": meta.get("tab_context_id"),
        "semantic_action": meta.get("semantic_action"),
        "semantic_action_confidence": meta.get("semantic_action_confidence"),
        "clipboard_transfer_id": meta.get("clipboard_transfer_id"),
    }
    for key in (
        "browser_hostname",
        "tab_context_id",
        "semantic_action",
        "semantic_action_confidence",
        "clipboard_transfer_id",
    ):
        if out.get(key) in (None, ""):
            out.pop(key, None)
    return out

def _case(case_id: str, events: list[dict[str, Any]], *, requires_uncertainty: bool = False, note: str = "") -> dict[str, Any]:
    workflows: dict[str, dict[str, Any]] = {}
    noise: list[str] = []
    for event in events:
        truth = event["_truth"]
        wid = truth["workflow"]
        if wid == "NOISE":
            noise.append(event["event_id"])
            continue
        row = workflows.setdefault(wid, {
            "workflow_id": wid,
            "event_ids": [],
            "checkpoint_groups": {},
            "automation_relevant_event_ids": [],
            "ui_mechanic_event_ids": [],
        })
        row["event_ids"].append(event["event_id"])
        cp = truth.get("checkpoint")
        if cp:
            row["checkpoint_groups"].setdefault(cp, []).append(event["event_id"])
        if truth["role"] == "checkpoint":
            row["automation_relevant_event_ids"].append(event["event_id"])
        elif truth["role"] == "ui":
            row["ui_mechanic_event_ids"].append(event["event_id"])
    wf_list = []
    for wid in sorted(workflows):
        row = workflows[wid]
        row["checkpoint_groups"] = [
            {"checkpoint_id": key, "event_ids": ids}
            for key, ids in row["checkpoint_groups"].items()
        ]
        wf_list.append(row)
    return {
        "case_id": case_id,
        "presented_evidence": [_trace_projection(e) for e in events],
        "source_events": [_source_event(e) for e in events],
        "ground_truth": {
            "workflows": wf_list,
            "noise_event_ids": noise,
            "requires_uncertainty": requires_uncertainty,
            "note": note,
        },
    }

def _scenario(kind: int, variant: int) -> dict[str, Any]:
    # Variants deliberately perturb timing, optional UI actions and missing resource
    # references without changing the hidden workflow memberships.
    v = variant
    events: list[dict[str, Any]] = []
    add = events.append
    i = 1
    def E(seconds: int, surface: str, action: str, **kwargs: Any) -> None:
        nonlocal i
        add(_event(i, seconds + v * 3, surface, action, **kwargs))
        i += 1

    if kind == 1:
        E(0,"gmail","click",label="Open renewal",resource=f"mail-a{v}",workflow="A",checkpoint="request",tab="tab-a")
        E(20,"salesforce","click",label="Open account",resource=f"acct-a{v}",workflow="A",checkpoint="account",tab="tab-sf-a")
        E(40,"slack","focus",label="New message",resource=None,workflow="B",checkpoint="support_request")
        E(55,"jira","click",label="Open issue",resource=f"jira-b{v}",workflow="B",checkpoint="issue",tab="tab-jira-b")
        E(80,"salesforce","click",label="Return to account",resource=f"acct-a{v}",workflow="A",role="ui",tab="tab-sf-a")
        E(100,"sheets","click",label="Check pricing",resource=f"sheet-a{v}",workflow="A",checkpoint="pricing",tab="tab-sheet-a")
        E(120,"jira","click",label="Add note",resource=f"jira-b{v}",workflow="B",checkpoint="update",tab="tab-jira-b")
        E(145,"salesforce","click",label="Save renewal",resource=f"acct-a{v}",workflow="A",checkpoint="crm_update",tab="tab-sf-a")
        E(165,"gmail","click",label="Send reply",resource=f"mail-a{v}",workflow="A",checkpoint="reply",tab="tab-a")
    elif kind == 2:
        E(0,"gmail","click",label="Open message",resource=f"mail-a{v}",workflow="A",checkpoint="request",tab="tab-a")
        E(18,"gmail","click",label="Reply",resource=f"mail-a{v}",workflow="A",role="ui",tab="tab-a")
        E(34,"gmail","click",label="Open message",resource=f"mail-b{v}",workflow="B",checkpoint="request",tab="tab-b")
        E(48,"salesforce","click",label="Open account",resource=f"acct-b{v}",workflow="B",checkpoint="account",tab="tab-sf-b")
        E(68,"gmail","click",label="Return message",resource=f"mail-a{v}",workflow="A",role="ui",tab="tab-a")
        E(82,"salesforce","click",label="Open account",resource=f"acct-a{v}",workflow="A",checkpoint="account",tab="tab-sf-a")
        E(100,"gmail","click",label="Send reply",resource=f"mail-a{v}",workflow="A",checkpoint="reply",tab="tab-a")
        E(118,"gmail","click",label="Return message",resource=f"mail-b{v}",workflow="B",role="ui",tab="tab-b")
        E(132,"gmail","click",label="Send reply",resource=f"mail-b{v}",workflow="B",checkpoint="reply",tab="tab-b")
    elif kind == 3:
        E(0,"salesforce","click",label="Open opportunity",resource=f"opp-a{v}",workflow="A",checkpoint="open",tab="tab-a")
        E(18,"salesforce","click",label="Edit stage",resource=f"opp-a{v}",workflow="A",checkpoint="edit",tab="tab-a")
        E(32,"salesforce","click",label="Open opportunity",resource=f"opp-b{v}",workflow="B",checkpoint="open",tab="tab-b")
        E(48,"salesforce","click",label="Edit owner",resource=f"opp-b{v}",workflow="B",checkpoint="edit",tab="tab-b")
        E(64,"salesforce","click",label="Return Opportunity A",resource=f"opp-a{v}",workflow="A",role="ui",tab="tab-a")
        E(80,"salesforce","click",label="Save",resource=f"opp-a{v}",workflow="A",checkpoint="save",tab="tab-a")
        E(96,"salesforce","click",label="Return Opportunity B",resource=f"opp-b{v}",workflow="B",role="ui",tab="tab-b")
        E(112,"salesforce","click",label="Save",resource=f"opp-b{v}",workflow="B",checkpoint="save",tab="tab-b")
    elif kind == 4:
        E(0,"outlook","focus",label="Finance request",resource=None,workflow="C",checkpoint="request")
        E(15,"gmail","click",label="Renewal request",resource=f"mail-a{v}",workflow="A",checkpoint="request",tab="tab-a")
        E(30,"slack","focus",label="Support request",resource=None,workflow="B",checkpoint="request")
        E(48,"salesforce","click",label="Open account",resource=f"acct-a{v}",workflow="A",checkpoint="account",tab="tab-sf-a")
        E(66,"excel","focus",label="Rate table",resource=None,workflow="C",checkpoint="rate")
        E(84,"jira","click",label="Open issue",resource=f"jira-b{v}",workflow="B",checkpoint="ticket",tab="tab-b")
        E(104,"pricing","click",label="Update rate",resource=None,workflow="C",checkpoint="update")
        E(122,"sheets","click",label="Open spreadsheet",resource=f"sheet-a{v}",workflow="A",checkpoint="pricing",tab="tab-sheet")
        E(140,"jira","click",label="Resolve ticket",resource=f"jira-b{v}",workflow="B",checkpoint="resolve",tab="tab-b")
        E(158,"gmail","click",label="Send renewal",resource=f"mail-a{v}",workflow="A",checkpoint="reply",tab="tab-a")
        E(176,"pricing","click",label="Save rate",resource=None,workflow="C",checkpoint="save")
    elif kind == 5:
        E(0,"gmail","click",label="Open renewal",resource=f"mail-a{v}",workflow="A",checkpoint="request",tab="tab-a")
        E(25,"salesforce","click",label="Open account",resource=f"acct-a{v}",workflow="A",checkpoint="account",tab="tab-a2")
        E(50,"github","click",label="Review PR",resource=f"pr-b{v}",workflow="B",checkpoint="review",tab="tab-b")
        E(85,"github","click",label="Comment PR",resource=f"pr-b{v}",workflow="B",checkpoint="comment",tab="tab-b")
        E(260,"github","click",label="Approve PR",resource=f"pr-b{v}",workflow="B",checkpoint="approve",tab="tab-b")
        E(420,"salesforce","click",label="Return account",resource=f"acct-a{v}",workflow="A",role="ui",tab="tab-a2")
        E(440,"gmail","click",label="Send renewal",resource=f"mail-a{v}",workflow="A",checkpoint="reply",tab="tab-a")
    elif kind == 6:
        shared=f"sheet-shared{v}"
        E(0,"gmail","click",label="Open message",resource=f"mail-a{v}",workflow="A",checkpoint="request",tab="tab-a")
        E(20,"sheets","click",label="Open row",resource=shared,workflow="A",checkpoint="pricing",tab="tab-shared")
        E(38,"outlook","focus",label="Open request",resource=None,workflow="B",checkpoint="request")
        E(56,"sheets","click",label="Open row",resource=shared,workflow="B",checkpoint="rate",tab="tab-shared")
        E(76,"salesforce","click",label="Save record",resource=f"acct-a{v}",workflow="A",checkpoint="update",tab="tab-sf")
        E(96,"pricing","click",label="Save record",resource=None,workflow="B",checkpoint="update")
        E(116,"gmail","click",label="Send reply",resource=f"mail-a{v}",workflow="A",checkpoint="reply",tab="tab-a")
        E(136,"pricing","click",label="Save",resource=None,workflow="B",checkpoint="save")
    elif kind == 7:
        E(0,"gmail","click",label="Open renewal",resource=f"mail-a{v}",workflow="A",checkpoint="request",tab="tab-a")
        E(20,"salesforce","click",label="Account",resource=f"acct-a{v}",workflow="A",checkpoint="account",tab="tab-sf")
        E(40,"sheets","click",label="Pricing",resource=f"sheet-a{v}",workflow="A",checkpoint="pricing",tab="tab-sheet")
        E(60,"slack","focus",label="Request manager approval",resource=None,workflow="A",checkpoint="approval")
        E(90,"salesforce","click",label="Save renewal",resource=f"acct-a{v}",workflow="A",checkpoint="update",tab="tab-sf")
        E(108,"gmail","click",label="Send reply",resource=f"mail-a{v}",workflow="A",checkpoint="reply",tab="tab-a")
        E(125,"jira","click",label="Open issue",resource=f"jira-b{v}",workflow="NOISE",role="noise",tab="tab-noise")
    elif kind == 8:
        E(0,"gmail","click",label="Open order change",resource=f"mail-a{v}",workflow="A",checkpoint="request",tab="tab-a")
        E(18,"salesforce","click",label="Customer record",resource=f"acct-a{v}",workflow="A",checkpoint="account",tab="tab-sf",omit_resource=(v%2==0))
        E(35,"slack","focus",label="Open message",resource=None,workflow="B",checkpoint="request")
        E(52,"docs","click",label="Open document",resource=f"doc-b{v}",workflow="B",checkpoint="document",tab="tab-b",omit_resource=(v%2==1))
        E(72,"salesforce","click",label="Return customer",resource=f"acct-a{v}",workflow="A",role="ui",tab="tab-sf",omit_resource=(v%2==0))
        E(90,"gmail","click",label="Send order change",resource=f"mail-a{v}",workflow="A",checkpoint="reply",tab="tab-a")
        E(108,"docs","click",label="Edit document",resource=f"doc-b{v}",workflow="B",checkpoint="edit",tab="tab-b",omit_resource=(v%2==1))
    elif kind == 9:
        transfer=f"xfer-{v}"
        E(0,"sheets","click",label="Copy approved price",resource=f"sheet-a{v}",workflow="A",checkpoint="source",tab="tab-sheet",clipboard_transfer=transfer,clipboard_action="copy")
        E(18,"slack","focus",label="New message",resource=None,workflow="B",checkpoint="request")
        E(34,"jira","click",label="Open issue",resource=f"jira-b{v}",workflow="B",checkpoint="issue",tab="tab-b")
        E(52,"salesforce","click",label="Paste approved price",resource=f"acct-a{v}",workflow="A",checkpoint="destination",tab="tab-sf",clipboard_transfer=transfer,clipboard_action="paste")
        E(70,"salesforce","click",label="Save account",resource=f"acct-a{v}",workflow="A",checkpoint="save",tab="tab-sf")
        E(88,"jira","click",label="Resolve issue",resource=f"jira-b{v}",workflow="B",checkpoint="resolve",tab="tab-b")
    else:
        E(0,"github","click",label="Open PR",resource=f"pr-a{v}",workflow="A",checkpoint="review",tab="tab-a")
        E(18,"terminal","focus",label="Agent starts tests",resource=None,workflow="A",checkpoint="agent_test",actor_kind="agent")
        E(36,"gmail","click",label="Open message",resource=f"mail-b{v}",workflow="B",checkpoint="request",tab="tab-b")
        E(54,"terminal","focus",label="Agent reports failing test",resource=None,workflow="A",checkpoint="agent_failure",actor_kind="agent")
        E(74,"github","click",label="Inspect failing file",resource=f"pr-a{v}",workflow="A",checkpoint="inspect",tab="tab-a")
        E(94,"gmail","click",label="Send reply",resource=f"mail-b{v}",workflow="B",checkpoint="reply",tab="tab-b")
        E(114,"github","click",label="Push fix",resource=f"pr-a{v}",workflow="A",checkpoint="fix",tab="tab-a")

    # Each variant changes structure, not merely IDs/timing. This prevents the
    # corpus from being ten templates repeated three times while retaining the
    # same scenario family for controlled analysis.
    if v == 1 and len(events) >= 5:
        # Add realistic passive/noise activity at a non-terminal point.
        insert_at = max(2, len(events) // 2)
        noise = _event(
            900 + kind,
            int((datetime.fromisoformat(events[insert_at]["observed_at"].replace("Z", "+00:00")) - datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)).total_seconds()) - v * 3 + 7,
            "docs",
            "click",
            label="Open document",
            resource=f"reference-{kind}-{v}",
            workflow="NOISE",
            role="noise",
            tab=f"tab-reference-{kind}",
        )
        events.insert(insert_at, noise)
    elif v == 2 and len(events) >= 6:
        # Remove one UI-only return when available, then add a same-surface
        # distractor. Membership/order therefore differs from v1/v2.
        ui_index = next((idx for idx, e in enumerate(events) if e["_truth"]["role"] == "ui"), None)
        if ui_index is not None:
            events.pop(ui_index)
        insert_at = min(3, len(events) - 1)
        noise = _event(
            950 + kind,
            int((datetime.fromisoformat(events[insert_at]["observed_at"].replace("Z", "+00:00")) - datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)).total_seconds()) - v * 3 + 5,
            "gmail" if kind % 2 else "salesforce",
            "click",
            label="Open item",
            resource=f"distractor-{kind}-{v}",
            workflow="NOISE",
            role="noise",
            tab=f"tab-distractor-{kind}",
        )
        events.insert(insert_at, noise)

    return _case(
        f"reconstruction-{kind:02d}-v{variant+1}",
        events,
        requires_uncertainty=(kind == 7),
        note=("Approval was observed, but the trigger rule is deliberately absent from evidence." if kind == 7 else ""),
    )

def _structural_signature(case: dict[str, Any]) -> tuple[Any, ...]:
    truth_by_event: dict[str, str] = {}
    for workflow in (case.get("ground_truth") or {}).get("workflows") or []:
        for event_id in workflow.get("event_ids") or []:
            truth_by_event[str(event_id)] = "workflow"
    for event_id in (case.get("ground_truth") or {}).get("noise_event_ids") or []:
        truth_by_event[str(event_id)] = "noise"
    return tuple(
        (
            event.get("app"),
            event.get("event_type"),
            bool((event.get("metadata") or {}).get("resource_reference")),
            truth_by_event.get(str(event.get("event_id")), "unknown"),
        )
        for event in case.get("presented_evidence") or []
    )

def generate_cases() -> list[dict[str, Any]]:
    return [_scenario(kind, variant) for kind in range(1, 11) for variant in range(3)]

def write_cases(path: Path) -> None:
    cases = generate_cases()
    path.write_text(
        "".join(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in cases),
        encoding="utf-8",
    )

def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        return generate_cases()
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows

def validate_cases(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    for case in cases:
        cid = str(case.get("case_id") or "")
        if not cid or cid in ids:
            errors.append(f"duplicate or missing case_id: {cid!r}")
        ids.add(cid)
        events = case.get("presented_evidence") or []
        event_ids = [str(e.get("event_id") or "") for e in events]
        if len(event_ids) != len(set(event_ids)):
            errors.append(f"{cid}: duplicate event ids")
        if any("_truth" in e or "workflow_id" in e or "checkpoint_id" in e for e in events):
            errors.append(f"{cid}: hidden ground truth leaked into presented evidence")
        if any(any(key in e for key in ("actor_id", "device_id", "sensor_id", "schema_version")) for e in events):
            errors.append(f"{cid}: internal identity fields leaked into AI-facing evidence")
        if any("session_id" not in e for e in events):
            errors.append(f"{cid}: session_id missing from AI-facing trace fixture")
        source_events = case.get("source_events") or []
        if any("_truth" in e or "workflow_id" in e or "checkpoint_id" in e for e in source_events):
            errors.append(f"{cid}: hidden ground truth leaked into source events")
        if [str(e.get("event_id") or "") for e in source_events] != event_ids:
            errors.append(f"{cid}: source/presented event IDs differ")
        truth = case.get("ground_truth") or {}
        used: list[str] = []
        for workflow in truth.get("workflows") or []:
            used.extend(str(x) for x in workflow.get("event_ids") or [])
        used.extend(str(x) for x in truth.get("noise_event_ids") or [])
        if sorted(used) != sorted(event_ids):
            missing = sorted(set(event_ids) - set(used))
            extra = sorted(set(used) - set(event_ids))
            errors.append(f"{cid}: truth partition mismatch missing={missing} extra={extra}")
        revealing = ("unrelated", "different project", "customer alpha", "customer beta", "ticket b", "update b", "opportunity a", "opportunity b")
        visible_text = " ".join(
            str(value).lower()
            for event in events
            for value in (
                event.get("window_title", ""),
                event.get("target_label", ""),
                ((event.get("metadata") or {}).get("page") or {}).get("title", ""),
            )
        )
        if any(token in visible_text for token in revealing):
            errors.append(f"{cid}: answer-revealing synthetic label leaked into presented evidence")
    signatures = [_structural_signature(case) for case in cases]
    if len(set(signatures)) != len(cases):
        errors.append("corpus contains structurally duplicate cases")
    return errors

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate deterministic reconstruction cases")
    parser.add_argument("--output")
    args = parser.parse_args()
    cases = generate_cases()
    errors = validate_cases(cases)
    if errors:
        raise SystemExit("\n".join(errors))
    if args.output:
        output = Path(args.output)
        write_cases(output)
        print(f"wrote {len(cases)} reconstruction cases to {output}")
    else:
        print(f"validated {len(cases)} deterministic reconstruction cases")
