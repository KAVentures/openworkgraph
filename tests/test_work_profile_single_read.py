"""One read of context events must feed the complete work-profile projection."""
from __future__ import annotations

from server.work_profile import service


def test_current_profile_reuses_exact_loaded_context(monkeypatch):
    context = [{
        "event_id": "one", "observed_at": "2026-10-14T09:00:00Z",
        "surface": "Gmail", "action": "copy", "metadata": {"test": True},
    }]
    counters = {"load": 0}
    monkeypatch.setattr(service, "backfill_clipboard_context_links", lambda: 0)
    monkeypatch.setattr(service, "_scope_since", lambda scope, now=None:
                        "2026-10-14T00:00:00Z" if scope == "current" else None)

    def load(since):
        counters["load"] += 1
        assert since == "2026-10-14T00:00:00Z"
        return context

    def core(*, scope, now, _preloaded_context):
        assert scope == "current"
        assert _preloaded_context is context
        return {"scope": scope, "since": "2026-10-14T00:00:00Z", "result": 42}

    def enrich(profile, *, since, rows):
        assert rows is context
        assert since == "2026-10-14T00:00:00Z"
        return profile | {"signal": True}

    monkeypatch.setattr(service, "_context_rows", load)
    monkeypatch.setattr(service, "_compute_work_profile", core)
    monkeypatch.setattr(service, "enrich_work_profile", enrich)
    result = service.compute_work_profile(scope="current")
    assert result["result"] == 42 and result["signal"] is True
    assert counters["load"] == 1


def test_today_profile_reuses_context_and_invalid_scope_fails_before_loading(monkeypatch):
    context = []
    calls = []
    monkeypatch.setattr(service, "backfill_clipboard_context_links", lambda: 0)
    monkeypatch.setattr(service, "local_day_since", lambda now=None: "today")
    monkeypatch.setattr(service, "_context_rows", lambda since: calls.append(since) or context)
    monkeypatch.setattr(service, "compute_today_work_profile",
                        lambda now=None, _preloaded_context=None: {
                            "scope": "today", "since": "today",
                            "same_rows": _preloaded_context is context,
                        })
    monkeypatch.setattr(service, "enrich_work_profile",
                        lambda profile, **kwargs: profile | {
                            "enrichment_used_same_rows": kwargs["rows"] is context,
                        })
    result = service.compute_work_profile(scope="today")
    assert result["same_rows"] and result["enrichment_used_same_rows"]
    assert calls == ["today"]
