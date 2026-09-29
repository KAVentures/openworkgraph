from __future__ import annotations

"""Titles keep their context; only sensitive details become stable tokens."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
import json, re
from datetime import datetime, timedelta, timezone
from browser_title_privacy import protect_text, protect_titles_at_rest, protect_path
from server.db import init_db, insert_events, connect, protect_existing_titles
init_db()
base = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)
def stored(event_id):
    with connect() as c:
        row = c.execute("SELECT window_title, metadata_json FROM events WHERE event_id = ?", (event_id,)).fetchone()
    return row[0], json.loads(row[1])
'''


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "c.json"), "PYTHONPATH": str(ROOT)})
    result = subprocess.run([sys.executable, "-c", PRELUDE + code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_business_context_is_kept_and_personal_details_are_tokenized(tmp_path):
    _run(r'''
keep = [
    "Acme Logistics AB | Account | Salesforce",
    "Q4 pipeline - Customer tracker - Google Sheets",
    "Projektplan Volvo Cars 2026.docx",
    "GitHub - KAVentures/openworkgraph: Pull request #112",
    "Mötesanteckningar styrelsemöte 12 oktober - Google Docs",
    "Invoice INV-2024-00931 - Fortnox",
    "Order 4471-2231 - Zendesk",
]
for title in keep:
    assert protect_text(title) == title, (title, protect_text(title))

cases = {
    "Re: Contract renewal Q4 - Anna Svensson <anna.svensson@acme.se> - Gmail":
        r"Re: Contract renewal Q4 - PERSON_\w{6} <EMAIL_\w{6}> - Gmail",
    "Erik Lund (DM) - Kinvectum - Slack": r"PERSON_\w{6} \(DM\) - Kinvectum - Slack",
    "Möte med Karin Ek om budget 2027 - Outlook": r"Möte med PERSON_\w{6} om budget 2027 - Outlook",
    "Order 4471-2231 – Call +46 70 123 45 67 - Zendesk": r"Order 4471-2231 – Call PHONE_\w{6} - Zendesk",
    "Patient 19850312-1234 remiss - Journal": r"Patient PERSONNUMMER_\w{6} remiss - Journal",
    "IBAN SE45 5000 0000 0583 9825 7466 payment - Bank": r"IBAN IBAN_\w{6} payment - Bank",
    "Card 4111 1111 1111 1111 declined - Stripe": r"Card PAYMENT_CARD_\w{6} declined - Stripe",
    "Meeting with Dr. Lindqvist about Q3 budget - Calendar": r"Meeting with Dr\. PERSON_\w{6} about Q3 budget - Calendar",
}
for title, pattern in cases.items():
    out = protect_text(title)
    assert re.fullmatch(pattern, out), (title, out)
    assert protect_text(out) == out  # idempotent: tokens are never re-tokenized
assert protect_path("/people/anna-svensson") .startswith("/people/PERSON_")
assert protect_path("/lightning/r/Account/001Dn00000ABCdE/view") == "/lightning/r/Account/001Dn00000ABCdE/view"
''', tmp_path)


def test_stored_events_keep_context_surface_and_one_token_per_person(tmp_path):
    _run(r'''
title = "Re: Contract renewal Q4 - Anna Svensson <anna.svensson@acme.se> - Gmail"
insert_events([
    {"event_id": "focus", "observed_at": base.isoformat(), "device_id": "d", "session_id": "s", "app": "Google Chrome",
     "window_title": title, "event_type": "focus_span", "duration_seconds": 60, "metadata": {"source": "desktop"}},
    {"event_id": "click", "observed_at": (base + timedelta(seconds=5)).isoformat(), "device_id": "d", "session_id": "s",
     "source": "browser_extension", "app": "Google Chrome", "window_title": title, "event_type": "browser_click", "duration_seconds": 0,
     "metadata": {"source": "browser_extension", "action": "click",
                  "page": {"hostname": "mail.google.com", "pathname": "/people/anna-svensson", "title": title},
                  "target": {"tag": "button", "label": "Reply to Anna Svensson"}}},
    {"event_id": "later", "observed_at": (base + timedelta(minutes=5)).isoformat(), "device_id": "d", "session_id": "s", "app": "Slack",
     "window_title": "Anna Svensson (DM) - Slack", "event_type": "focus_span", "duration_seconds": 30, "metadata": {"source": "desktop"}},
])
focus_title, _ = stored("focus")
click_title, click_meta = stored("click")
later_title, _ = stored("later")
person = re.search(r"PERSON_\w{6}", focus_title).group(0)
assert focus_title == click_title == click_meta["page"]["title"]
assert focus_title.startswith("Re: Contract renewal Q4 - ") and focus_title.endswith(" - Gmail")
assert click_meta["page"]["surface"] == "Gmail"
assert click_meta["target"]["label"] == f"Reply to {person}"
assert later_title == f"{person} (DM) - Slack"  # the same person keeps the same token
with connect() as c:
    everything = json.dumps([dict(r) for r in c.execute("SELECT * FROM events").fetchall()]
                            + [dict(r) for r in c.execute("SELECT * FROM normalized_events").fetchall()]
                            + [dict(r) for r in c.execute("SELECT * FROM context_events").fetchall()])
assert "Anna" not in everything and "Svensson" not in everything and "anna.svensson" not in everything.lower()
''', tmp_path)


def test_exclusions_and_agent_events_are_unaffected(tmp_path):
    _run(r'''
from browser_privacy import harden_browser_event
excluded = harden_browser_event({"event_id": "x", "app": "Google Chrome", "window_title": "Bank - Anna Svensson", "event_type": "browser_click",
                                 "metadata": {"page": {"hostname": "bank.example.com", "title": "Bank - Anna Svensson"}}},
                                {"excluded_browser_host_patterns": ["bank.example.com"]})
assert protect_titles_at_rest(excluded)["window_title"] == ""
agent = {"event_id": "a", "source": "agent", "window_title": "", "metadata": {"source": "agent", "operation": "tool_call"}}
assert protect_titles_at_rest(agent) == agent
''', tmp_path)


def test_existing_titles_are_protected_once_on_upgrade(tmp_path):
    _run(r'''
# A row stored by an older version (written directly, bypassing today's rule).
with connect() as c:
    c.execute("INSERT INTO events(event_id, observed_at, device_id, session_id, app, window_title, event_type, duration_seconds, metadata_json)"
              " VALUES ('old', ?, 'd', 's', 'Slack', 'Erik Lund (DM) - Kinvectum - Slack', 'focus_span', 60, '{}')", (base.isoformat(),))
assert protect_existing_titles() == 1
title, meta = stored("old")
assert re.fullmatch(r"PERSON_\w{6} \(DM\) - Kinvectum - Slack", title) and meta["privacy"]["titles"] == "kept_with_sensitive_details_tokenized"
with connect() as c:
    assert "Erik" not in json.dumps([dict(r) for r in c.execute("SELECT * FROM normalized_events").fetchall()])
    c.execute("UPDATE events SET window_title = 'Erik Lund again' WHERE event_id = 'old'")
assert protect_existing_titles() == 0  # runs once per data folder
''', tmp_path)
