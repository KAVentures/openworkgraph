from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = r"PERSON_[0-9A-F]{6}"


@pytest.fixture
def ai(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WORKFLOW_OBSERVER_CONFIG", str(tmp_path / "config.json"))
    from server import ai_context
    return ai_context


def _title(ai, text: str, field: str = "window_title") -> str:
    return ai.redact_contextually({field: text})[field]


@pytest.mark.parametrize("raw,expected", [
    ("Re: Contract for Anna Svensson - Gmail", rf"^Re: Contract for {TOKEN} - Gmail$"),
    ("Open email from Anna Svensson", rf"^Open email from {TOKEN}$"),
    ("Faktura 4471 från Erik Lindqvist - Outlook", rf"^Faktura 4471 från {TOKEN} - Outlook$"),
    ("Microsoft Teams - Chat with Sara Ek", rf"^Microsoft Teams - Chat with {TOKEN}$"),
    ("Call Dr. Lindqvist about lab results", rf"^Call Dr\. {TOKEN} about lab results$"),
    ("Select Anna Svensson, Contract renewal", rf"^Select {TOKEN}, Contract renewal$"),
    ("Meeting with Johan and Anna re Q3 budget - Google Calendar",
     rf"^Meeting with ({TOKEN}) and ({TOKEN}) re Q3 budget - Google Calendar$"),
])
def test_replace_only_the_sensitive_span(ai, raw, expected):
    out = _title(ai, raw)
    match = re.fullmatch(expected, out)
    assert match, out
    if match.groups():
        assert match.group(1) != match.group(2)  # two different people


@pytest.mark.parametrize("raw", [
    "Acme AB - Account - Salesforce",
    "Google Sheets - Q3 pipeline tracker",
    "May report - Google Docs",
    "Chat with Claude",
    "Rose Garden Hotel proposal - Google Docs",
    "Report for Ericsson - Q3",
])
def test_non_sensitive_titles_unchanged(ai, raw):
    assert _title(ai, raw) == raw


def test_same_person_same_token_across_apps_and_fields(ai):
    a = _title(ai, "Re: Contract for Anna Svensson - Gmail")
    b = _title(ai, "Anna Svensson - Contact - Salesforce")
    c = _title(ai, "Select Anna Svensson, Contract renewal", field="label")
    tokens = {re.search(TOKEN, x).group(0) for x in (a, b, c)}
    assert len(tokens) == 1


def test_typed_tokens(ai):
    out = _title(ai, "Patient Karin Berg, 19850312-1234, anna@acme.se, +46 70 123 45 67, order 123456789012")
    assert re.search(r"PERSON_[0-9A-F]{6}", out)
    assert re.search(r"PERSONNUMMER_[0-9A-F]{6}", out)
    assert re.search(r"EMAIL_[0-9A-F]{6}", out)
    assert re.search(r"PHONE_[0-9A-F]{6}", out)
    assert re.search(r"ID_[0-9A-F]{6}", out)
    assert not re.search(r"Karin|Berg|19850312|anna@|123456789012", out)


def test_learned_identity_is_redacted_everywhere_afterwards(ai):
    """Regression: an identity seen once as "Name <email>" leaked in later titles."""
    from server.privacy_pipeline import redact_for_display

    first = redact_for_display({"window_title": "Zlatko Wrzesniewski <zlatko.w@firm.pl> - Inbox"})
    token = re.search(TOKEN, first["window_title"]).group(0)
    # Later, separate display calls and AI-context calls, other fields:
    assert redact_for_display({"window_title": "Contract Zlatko Wrzesniewski - Gmail"})["window_title"] == f"Contract {token} - Gmail"
    assert _title(ai, "Contract Zlatko Wrzesniewski - Gmail") == f"Contract {token} - Gmail"
    assert ai.redact_contextually({"label": "Zlatko Wrzesniewski, Q3 plan"})["label"] == f"{token}, Q3 plan"
    # Read paths never write the registry file.
    assert not (Path(os.environ["WORKFLOW_OBSERVER_DATA"]) / ".presentation_people.json").exists()


def test_reset_forgets_display_learned_identities(ai):
    from server.privacy_pipeline import redact_for_display, reset_persistent_identities

    redact_for_display({"window_title": "Zlatko Wrzesniewski <zlatko.w@firm.pl> - Inbox"})
    reset_persistent_identities()
    assert redact_for_display({"window_title": "Contract Zlatko Wrzesniewski - Gmail"})["window_title"] == (
        "Contract Zlatko Wrzesniewski - Gmail"
    )


def test_never_and_always_redact_lists(ai):
    ai.save_user_settings(never_redact=["Anna Svensson AB", "Q3 pipeline"], always_redact=["Project Falcon"])
    assert _title(ai, "Invoice from Anna Svensson AB - Outlook") == "Invoice from Anna Svensson AB - Outlook"
    out = _title(ai, "Project Falcon kickoff - Google Docs")
    assert "Falcon" not in out and out.endswith(" kickoff - Google Docs")


def test_owner_stays_owner(ai):
    from server import presentation

    Path(os.environ["WORKFLOW_OBSERVER_CONFIG"]).write_text(json.dumps({"owner_aliases": ["Maja Owner"]}), encoding="utf-8")
    presentation._OWNER_CACHE.clear()
    try:
        assert _title(ai, "Meeting with Maja Owner - Google Calendar") == "Meeting with OWNER - Google Calendar"
    finally:
        presentation._OWNER_CACHE.clear()


def test_settings_validation_and_org_lock(ai, tmp_path, monkeypatch):
    assert ai.effective_detail()["detail_level"] == "redacted"  # default
    ai.save_user_settings(detail="full")
    assert ai.effective_detail()["detail_level"] == "full"
    with pytest.raises(ValueError):
        ai.save_user_settings(detail="raw")

    # Gateway policy lock (cached by the sync worker) only applies while enrolled.
    from connector.state import SyncState
    data = Path(os.environ["WORKFLOW_OBSERVER_DATA"])
    data.mkdir(parents=True, exist_ok=True)
    SyncState(data / "gateway_sync_state.db").set_bool("org_force_redacted_ai_context", True)
    assert ai.effective_detail()["detail_level"] == "full"
    cfg = json.loads(Path(os.environ["WORKFLOW_OBSERVER_CONFIG"]).read_text(encoding="utf-8"))
    cfg["gateway"] = {"enabled": True}
    Path(os.environ["WORKFLOW_OBSERVER_CONFIG"]).write_text(json.dumps(cfg), encoding="utf-8")
    state = ai.effective_detail()
    assert state["detail_level"] == "redacted" and state["lock_source"] == "gateway_policy"


def test_policy_merge_is_restrictive_for_ai_context():
    from connector.policy import merge_policies
    from gateway.policy import normalize_policy as gateway_normalize

    assert merge_policies({}, {})["force_redacted_ai_context"] is False
    assert merge_policies({}, {"force_redacted_ai_context": True})["force_redacted_ai_context"] is True
    assert merge_policies({"force_redacted_ai_context": True}, {})["force_redacted_ai_context"] is True
    assert gateway_normalize({"force_redacted_ai_context": True})["force_redacted_ai_context"] is True


def test_redaction_eval_meets_targets():
    spec = importlib.util.spec_from_file_location("redaction_eval", ROOT / "scripts" / "redaction_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Separate process: isolated data dir and no learned state from other tests.
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "redaction_eval.py")], cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    print(out.stdout.strip())
    numbers = dict(re.findall(r"(name_recall|over_redaction)=([0-9.]+)", out.stdout))
    assert float(numbers["name_recall"]) >= 0.95
    assert float(numbers["over_redaction"]) <= 0.05
    rows = module.load_rows()
    assert len(rows) >= 200


def test_typical_title_redacts_in_under_a_millisecond(ai):
    from server.contextual_redaction import Detector
    from server.presentation import _token

    rows = [json.loads(line)["text"] for line in (ROOT / "tests" / "fixtures" / "redaction_eval.jsonl").read_text(encoding="utf-8").splitlines()]
    detector = Detector(token=_token)
    for text in rows:  # warm-up
        detector.redact(text)
    start = time.perf_counter()
    for _ in range(5):
        for text in rows:
            detector.redact(text)
    per_title = (time.perf_counter() - start) / (5 * len(rows))
    print(f"uncached contextual redaction: {per_title * 1e6:.0f} us/title")
    assert per_title < 0.001

    cached = ai.contextual_text_redactor()
    for text in rows:
        cached(text)
    start = time.perf_counter()
    for text in rows:
        cached(text)
    assert (time.perf_counter() - start) / len(rows) < 0.0001
