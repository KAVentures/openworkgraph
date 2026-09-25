from __future__ import annotations

from pathlib import Path
import sys
import types

from adapters.openai_agents import OpenWorkGraphTracingProcessor, install_openai_agents_processor


def test_install_uses_additive_sdk_registration(monkeypatch):
    registered: list[object] = []

    tracing = types.ModuleType("agents.tracing")
    tracing.add_trace_processor = registered.append
    agents = types.ModuleType("agents")
    agents.tracing = tracing

    monkeypatch.setitem(sys.modules, "agents", agents)
    monkeypatch.setitem(sys.modules, "agents.tracing", tracing)

    processor = install_openai_agents_processor()
    assert isinstance(processor, OpenWorkGraphTracingProcessor)
    assert registered == [processor]
    processor.shutdown()


def test_core_openworkgraph_does_not_require_openai_agents_dependency():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "openai-agents" not in pyproject.lower()
