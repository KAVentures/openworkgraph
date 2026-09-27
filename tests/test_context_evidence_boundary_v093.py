from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_context_trace_is_not_filtered_by_observe_connection_state():
    """Observe state may filter the Agents UI, never canonical Context history."""
    trace_source = (ROOT / "server" / "mcp_trace.py").read_text(encoding="utf-8")
    agent_view_source = (ROOT / "server" / "agent_execution_trace_routes.py").read_text(encoding="utf-8")

    assert "hidden_frameworks" not in trace_source
    assert "observation_active" not in trace_source
    assert "hide_disconnected" not in trace_source

    # The connection-aware filtering introduced for the Agents tab remains explicitly
    # opt-in on the derived agent-run view instead of mutating stored evidence.
    assert "hide_disconnected: bool = False" in agent_view_source
    assert "hidden_frameworks" in agent_view_source


def test_context_trace_declares_task_inference_non_authoritative():
    trace_source = (ROOT / "server" / "mcp_trace.py").read_text(encoding="utf-8")
    assert '"derived_task_inference_authoritative": False' in trace_source
    assert '"data_layer": "privacy_hardened_raw_rich_evidence"' in trace_source
