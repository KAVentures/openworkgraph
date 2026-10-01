from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import resource_references
from demo_data import (
    build_agent_demo_payloads,
    build_agent_handoff_human_events,
    build_human_demo_events,
    demo_resource_references,
)
from shared.agent_ingress_validation import validate_agent_ingress_event


ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _fake_refs(monkeypatch):
    monkeypatch.setattr(resource_references, "_browser_pairing_secret", lambda: b"demo-browser-secret")
    monkeypatch.setattr(resource_references, "_storage_secret", lambda: b"demo-storage-secret")
    return demo_resource_references()


def test_primary_demo_is_three_human_only_workflows_with_real_evidence_shapes(monkeypatch):
    refs = _fake_refs(monkeypatch)
    events = build_human_demo_events(BASE, refs=refs)

    assert len(events) == 36
    assert {event["source"] for event in events} == {"desktop", "browser_extension"}
    assert all((event.get("metadata") or {}).get("actor_kind") == "human" for event in events)
    assert all((event.get("metadata") or {}).get("demo_scenario") == "human_renewal" for event in events)
    assert not any(event["source"] == "agent" for event in events)

    types = Counter(event["event_type"] for event in events)
    assert types["clipboard_copy"] == 3
    assert types["clipboard_paste"] == 3
    assert types["focus_span"] == 15

    transfers: dict[str, list[str]] = defaultdict(list)
    for event in events:
        meta = event.get("metadata") or {}
        transfer = meta.get("clipboard_transfer_id")
        if transfer:
            transfers[str(transfer)].append(str(meta.get("action")))
            assert meta["clipboard_contents_captured"] is False
            assert meta["privacy"]["clipboard_contents"] is False
            assert "text" not in meta
    assert len(transfers) == 3
    assert all(sorted(actions) == ["copy", "paste"] for actions in transfers.values())


def test_context_demo_refs_are_keyed_and_do_not_keep_provider_locators(monkeypatch):
    refs = _fake_refs(monkeypatch)
    flat = [ref for case in refs for ref in case.values()]

    assert len(flat) == 9
    assert len({ref["resource_ref"] for ref in flat}) == 9
    assert all(ref["resource_ref"].startswith("owg:r:") for ref in flat)
    assert all("resolver_locator" not in ref for ref in flat)

    events = build_human_demo_events(BASE, refs=refs)
    referenced = [
        event["metadata"]["resource_reference"]
        for event in events
        if (event.get("metadata") or {}).get("resource_reference")
    ]
    assert referenced
    assert all(ref["resource_ref"].startswith("owg:r:") for ref in referenced)
    assert all("resolver_locator" not in ref for ref in referenced)
    page_paths = [
        str((event.get("metadata") or {}).get("page", {}).get("pathname") or "")
        for event in events
    ]
    assert not any("DEMOthread" in path or "006000" in path or "demoSheet" in path for path in page_paths)


def test_agent_example_is_separate_optional_structural_evidence():
    human_handoff = build_agent_handoff_human_events(BASE)
    agent_payloads = build_agent_demo_payloads(BASE)

    assert human_handoff
    assert all(event["source"] != "agent" for event in human_handoff)
    assert all((event.get("metadata") or {}).get("demo_scenario") == "human_agent_handoff" for event in human_handoff)

    validated = [validate_agent_ingress_event(payload, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)) for payload in agent_payloads]
    assert len(validated) == 3
    assert {payload["operation"] for payload in validated} == {"tool_call"}
    assert {payload["tool_name"] for payload in validated} == {"repository_search", "edit_files", "run_tests"}
    serialized = repr(validated).lower()
    for forbidden in ("prompt", "tool_result", "tool_arguments", "clipboard_contents"):
        assert forbidden not in serialized


def test_demo_launchers_share_the_live_private_runtime_and_ship_in_packages():
    mac = (ROOT / "START_ON_MAC.command").read_text(encoding="utf-8")
    mac_demo = (ROOT / "TRY_DEMO_ON_MAC.command").read_text(encoding="utf-8")
    win = (ROOT / "START_ON_WINDOWS.ps1").read_text(encoding="utf-8")
    win_demo = (ROOT / "TRY_DEMO_ON_WINDOWS.bat").read_text(encoding="utf-8")
    mac_package = (ROOT / "scripts" / "build_macos_release.sh").read_text(encoding="utf-8")
    win_package = (ROOT / "scripts" / "build_windows_release.ps1").read_text(encoding="utf-8")

    assert 'exec .venv/bin/python start.py --mode "$MODE"' in mac
    assert 'START_ON_MAC.command" --mode demo' in mac_demo
    assert '[ValidateSet("observe", "demo")]' in win
    assert '& $Python start.py --mode $Mode' in win
    assert 'START_ON_WINDOWS.ps1" -Mode demo' in win_demo
    assert "Python 3 is not installed" not in win_demo
    assert "Workflow Observer" not in win_demo

    assert "TRY_DEMO_OPENWORKGRAPH.command" in mac_package
    assert "TRY_DEMO_OPENWORKGRAPH.cmd" in win_package
    assert "AI agent is NOT required" in mac_package
    assert "AI agent is NOT required" in win_package
