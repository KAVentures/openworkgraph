from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from resource_references import normalize_resource_reference
from server.agent_ingest import ingest_agent_payloads
from server.browser_signal_settings import apply_profile, save_settings
from server.db import init_db, insert_events


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _activity(duration: int, keys: int, clicks: int = 1, scrolls: int = 0) -> dict:
    engaged = max(1, duration - 4)
    return {
        "foreground_seconds": duration,
        "engaged_seconds": engaged,
        "idle_seconds": duration - engaged,
        "active_input_seconds": min(engaged, max(2, keys / 3)),
        "keypress_count": keys,
        "click_count": clicks,
        "scroll_count": scrolls,
        "input_events": keys + clicks + scrolls,
    }


def _base_event(session: str, at: datetime, *, sensor: str, source: str, app: str, title: str, event_type: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": _iso(at),
        "schema_version": "1.0",
        "device_id": "demo-laptop",
        "sensor_id": sensor,
        "source": source,
        "session_id": session,
        "app": app,
        "window_title": title,
        "event_type": event_type,
        "duration_seconds": 0,
    }


def _focus(
    session: str,
    at: datetime,
    duration: int,
    *,
    app: str,
    title: str,
    activity: dict,
    scenario: str,
) -> dict:
    event = _base_event(
        session, at, sensor="demo:desktop", source="desktop", app=app, title=title, event_type="focus_span"
    )
    event["duration_seconds"] = duration
    event["metadata"] = {
        "source": "desktop",
        "excluded": False,
        "activity": activity,
        "privacy": {"key_identities": False, "typed_values": False, "clipboard_contents": False},
        "demo": True,
        "demo_scenario": scenario,
        "actor_kind": "human",
    }
    return event


def _browser(
    session: str,
    at: datetime,
    *,
    host: str,
    path: str,
    title: str,
    label: str,
    scenario: str,
    resource_reference: dict | None = None,
    action: str = "click",
) -> dict:
    event = _base_event(
        session,
        at,
        sensor="demo:browser",
        source="browser_extension",
        app="Google Chrome",
        title=title,
        event_type=f"browser_{action}",
    )
    metadata = {
        "source": "browser_extension",
        "action": action,
        "page": {"origin": f"https://{host}", "hostname": host, "pathname": path, "title": title},
        "target": {"tag": "button", "role": "button", "label": label},
        "privacy": {"typed_values": False, "clipboard_contents": False, "url_query": False, "url_fragment": False},
        "demo": True,
        "demo_scenario": scenario,
        "actor_kind": "human",
    }
    if resource_reference:
        metadata["resource_reference"] = resource_reference
    event["metadata"] = metadata
    return event


def _clipboard(
    session: str,
    at: datetime,
    *,
    kind: str,
    app: str,
    title: str,
    transfer_id: str,
    change_token: int,
    scenario: str,
) -> dict:
    event = _base_event(
        session,
        at,
        sensor="demo:desktop",
        source="desktop",
        app=app,
        title=title,
        event_type=f"clipboard_{kind}",
    )
    event["metadata"] = {
        "source": "desktop",
        "action": kind,
        "clipboard_transfer_id": transfer_id,
        "clipboard_change_token": change_token,
        "clipboard_contents_captured": False,
        "privacy": {"typed_values": False, "clipboard_contents": False},
        "demo": True,
        "demo_scenario": scenario,
        "actor_kind": "human",
    }
    return event


def _stored_ref(provider: str, kind: str, host: str, locator: str) -> dict:
    reference = normalize_resource_reference(
        {
            "provider": provider,
            "resource_kind": kind,
            "host": host,
            "resolver_locator": locator,
        },
        include_locator=False,
    )
    if reference is None:
        raise ValueError(f"invalid demo resource reference: {provider}/{kind}")
    return reference


def demo_resource_references() -> list[dict[str, dict]]:
    """Build fake Context-mode refs for three repeated human renewal cases.

    These are intentionally fake provider IDs. The real locator is used only in
    memory to derive OWG's installation-keyed token and is not retained in the
    returned reference, matching the Context privacy profile.
    """
    cases: list[dict[str, dict]] = []
    for index in range(1, 4):
        cases.append(
            {
                "gmail": _stored_ref(
                    "gmail", "thread_locator", "mail.google.com", f"web-thread:DEMOthread{index:04d}"
                ),
                "salesforce": _stored_ref(
                    "salesforce", "record", "demo.my.salesforce.com", f"Opportunity:00600000000000{index}AAA"
                ),
                "sheet": _stored_ref(
                    "google_drive", "spreadsheet", "docs.google.com", f"spreadsheet:demoSheet{index:04d}"
                ),
            }
        )
    return cases


def build_human_demo_events(base: datetime, refs: list[dict[str, dict]] | None = None) -> list[dict]:
    """Three ordinary human workflows. No AI agent is required for these rows."""
    session = "demo-human-session"
    refs = refs or [{"gmail": None, "salesforce": None, "sheet": None} for _ in range(3)]
    events: list[dict] = []
    for i in range(3):
        t = base + timedelta(minutes=i * 22)
        case_refs = refs[i]
        scenario = "human_renewal"
        transfer_id = f"demo-transfer-{i + 1}"
        events.extend(
            [
                _focus(
                    session, t, 125, app="Google Chrome", title="Renewal request - Gmail",
                    activity=_activity(125, 26, 3, 1), scenario=scenario,
                ),
                _browser(
                    session, t + timedelta(seconds=7), host="mail.google.com", path="/mail/u/0/inbox",
                    title="Renewal request - Gmail", label="Open renewal request", scenario=scenario,
                    resource_reference=case_refs.get("gmail"),
                ),
                _focus(
                    session, t + timedelta(seconds=125), 155, app="Google Chrome",
                    title="Renewal opportunity - Salesforce", activity=_activity(155, 34, 4, 2), scenario=scenario,
                ),
                _browser(
                    session, t + timedelta(seconds=140), host="demo.my.salesforce.com",
                    path="/lightning/r/Opportunity/RESOURCE_ID/view", title="Renewal opportunity - Salesforce",
                    label="Open opportunity", scenario=scenario, resource_reference=case_refs.get("salesforce"),
                ),
                _focus(
                    session, t + timedelta(seconds=280), 175, app="Google Chrome",
                    title="Renewal pricing - Google Sheets", activity=_activity(175, 64, 5, 3), scenario=scenario,
                ),
                _browser(
                    session, t + timedelta(seconds=295), host="docs.google.com",
                    path="/spreadsheets/d/RESOURCE_ID/edit", title="Renewal pricing - Google Sheets",
                    label="Open pricing sheet", scenario=scenario, resource_reference=case_refs.get("sheet"),
                ),
                _clipboard(
                    session, t + timedelta(seconds=432), kind="copy", app="Google Chrome",
                    title="Renewal pricing - Google Sheets", transfer_id=transfer_id,
                    change_token=100 + i, scenario=scenario,
                ),
                _focus(
                    session, t + timedelta(seconds=455), 135, app="Google Chrome",
                    title="Renewal opportunity - Salesforce", activity=_activity(135, 42, 4, 1), scenario=scenario,
                ),
                _clipboard(
                    session, t + timedelta(seconds=475), kind="paste", app="Google Chrome",
                    title="Renewal opportunity - Salesforce", transfer_id=transfer_id,
                    change_token=100 + i, scenario=scenario,
                ),
                _browser(
                    session, t + timedelta(seconds=510), host="demo.my.salesforce.com",
                    path="/lightning/r/Opportunity/RESOURCE_ID/view", title="Renewal opportunity - Salesforce",
                    label="Save opportunity", scenario=scenario, resource_reference=case_refs.get("salesforce"),
                    action="submit",
                ),
                _focus(
                    session, t + timedelta(seconds=590), 125, app="Google Chrome", title="Renewal reply - Gmail",
                    activity=_activity(125, 76, 3, 1), scenario=scenario,
                ),
                _browser(
                    session, t + timedelta(seconds=690), host="mail.google.com", path="/mail/u/0/inbox",
                    title="Renewal reply - Gmail", label="Send reply", scenario=scenario,
                    resource_reference=case_refs.get("gmail"), action="submit",
                ),
            ]
        )
    return events


def build_agent_demo_payloads(base: datetime) -> list[dict]:
    """One separate optional agent run, using the real structural ingest contract."""
    run_start = base + timedelta(minutes=69)
    common = {
        "organization_id": "demo-org",
        "actor_id": "agent:demo-coding-agent",
        "device_id": "demo-laptop",
        "sensor_id": "agent:demo-adapter",
        "agent_name": "Demo Coding Agent",
        "provider": "demo-provider",
        "framework": "demo-harness",
        "model": "demo-model",
        "status": "success",
        "observation_level": "native_trace",
        "run_id": "demo-agent-run-1",
        "trace_id": "demo-agent-trace-1",
        "workflow_id": "demo-agent-review-workflow",
    }
    steps = [
        (0, "repository_search", "search", "demo-agent-span-1", "demo-agent-root"),
        (38, "edit_files", "filesystem", "demo-agent-span-2", "demo-agent-root"),
        (92, "run_tests", "shell", "demo-agent-span-3", "demo-agent-root"),
    ]
    payloads: list[dict] = []
    for offset, tool_name, category, span_id, parent_span_id in steps:
        payloads.append(
            {
                **common,
                "event_id": f"demo-agent-{tool_name}",
                "observed_at": _iso(run_start + timedelta(seconds=offset)),
                "operation": "tool_call",
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "tool_name": tool_name,
                "tool_category": category,
                "duration_seconds": 12.0,
            }
        )
    return payloads


def build_agent_handoff_human_events(base: datetime) -> list[dict]:
    """Human work around the optional agent run, kept separate from core demo repetition."""
    session = "demo-human-agent-session"
    start = base + timedelta(minutes=67)
    scenario = "human_agent_handoff"
    return [
        _focus(
            session, start, 95, app="Visual Studio Code", title="Automation backlog",
            activity=_activity(95, 31, 4, 2), scenario=scenario,
        ),
        _focus(
            session, start + timedelta(minutes=4), 80, app="Visual Studio Code", title="Review agent changes",
            activity=_activity(80, 18, 5, 2), scenario=scenario,
        ),
        _focus(
            session, start + timedelta(minutes=6), 70, app="Google Chrome", title="Pull request review - GitHub",
            activity=_activity(70, 12, 4, 1), scenario=scenario,
        ),
    ]


def build_demo_events(base: datetime, refs: list[dict[str, dict]] | None = None) -> list[dict]:
    """Compatibility helper: all synthetic human evidence, including agent handoff context."""
    return build_human_demo_events(base, refs=refs) + build_agent_handoff_human_events(base)


def main() -> None:
    init_db()
    # The demo DB is isolated from live evidence. Context mode lets the sample
    # demonstrate object correlation while still discarding provider locators.
    save_settings(apply_profile("context"))

    run_started = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    try:
        base = datetime.fromisoformat(str(run_started).replace("Z", "+00:00")) + timedelta(minutes=5)
    except Exception:
        base = datetime.now(timezone.utc) - timedelta(minutes=90)

    refs = demo_resource_references()
    human_events = build_demo_events(base, refs=refs)
    human_inserted = insert_events(human_events)
    agent_result = ingest_agent_payloads(build_agent_demo_payloads(base))

    print(
        {
            "inserted_human_events": human_inserted,
            "human_workflow_executions": 3,
            "clipboard_transfers": 3,
            "browser_context_profile": "context",
            "optional_agent_example_events": int(agent_result.get("inserted") or 0),
            "agent_required_for_human_capture": False,
        }
    )


if __name__ == "__main__":
    main()
