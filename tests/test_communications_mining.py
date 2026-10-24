"""Communications mining over explicitly selected email/ticket exports (roadmap #326 D)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shared import communications_mining as cm

ROOT = Path(__file__).resolve().parents[1]
SECRET_TEXT = "TOPSECRETMARKER"
PERSONNUMMER = "19811218-9876"


def _msg(text, i=0, thread="t", direction="inbound", channel="email"):
    return cm.Message(datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i), text, thread, "", direction, channel)


def test_classification_uses_the_taxonomy_and_reports_ties_and_misses():
    intents = cm.parse_taxonomy(cm.DEFAULT_TAXONOMY)
    assert cm.classify("Please cancel my subscription", intents)[0] == "Cancellation"
    assert cm.classify("Var är mitt paket? Leveransen har inte kommit", intents)[0] == "Order or delivery status"
    assert cm.classify("I forgot my password and cannot log in", intents)[0] == "Account access"
    # Multi-word phrases outweigh single words.
    assert cm.classify("where is my order, the invoice was fine", intents)[0] == "Order or delivery status"
    label, scores = cm.classify("refund the payment", intents)
    assert label == "ambiguous" and set(scores) == {"Refund request", "Billing or invoice"}
    assert cm.classify("lovely weather", intents) == ("unclassified", {})
    assert cm.classify("the errors were refunded", intents)[0] == "unclassified"  # whole terms only


@pytest.mark.parametrize("spec", [
    None, {"format": "x", "intents": []}, {"format": cm.FORMAT, "intents": []},
    {"format": cm.FORMAT, "intents": [{"name": "A", "terms": []}]},
    {"format": cm.FORMAT, "intents": [{"name": "A", "terms": ["x"]}, {"name": "a", "terms": ["y"]}]},
    {"format": cm.FORMAT, "intents": [{"name": "Unclassified", "terms": ["x"]}]},
    {"format": cm.FORMAT, "intents": [{"name": "A", "terms": ["x"], "weight": 2}]},
    {"format": cm.FORMAT, "intents": [{"name": "A", "terms": ["x" * 81]}]},
])
def test_invalid_taxonomies_are_refused(spec):
    with pytest.raises(cm.CommunicationsError):
        cm.parse_taxonomy(spec)


def test_analysis_keeps_only_aggregates_and_never_entity_values():
    messages = []
    for n in range(6):
        messages += [
            _msg(f"I want a refund for order #{70000 + n}, charged 499 kr. My personnummer is {PERSONNUMMER}. "
                 f"Call +46 70 123 45 67 or mail anna{n}@example.com {SECRET_TEXT}", 2 * n, f"r{n}"),
            _msg("We have refunded you", 2 * n + 1, f"r{n}", "outbound"),
        ]
    report = cm.analyze(messages)
    report.pop("_events")
    dumped = json.dumps(report)
    for leaked in (SECRET_TEXT, PERSONNUMMER, "anna0@example.com", "70000", "499", "+46 70"):
        assert leaked not in dumped
    assert report["raw_text_retained"] is False and report["entity_values_returned"] is False
    entities = {e["type"]: e for e in report["entities"]}
    assert entities["personnummer"]["messages"] == 6 and entities["email_address"]["messages"] == 6
    assert entities["order_reference"]["mentions"] == 6 and "ticket_reference" not in entities  # no double count
    assert entities["money_amount"]["messages"] == 6 and entities["phone_number"]["messages"] == 6
    intents = {i["intent"]: i for i in report["intents"]}
    assert intents["outbound reply"]["messages"] == 6 and intents["outbound reply"]["share"] is None
    assert report["classified_messages"] == 6 and report["outbound_messages_not_classified"] is True
    assert report["threads"]["first_response_seconds"] == {"median": 3600.0, "count": 6}


def test_themes_need_five_messages_and_exclude_redacted_identifiers():
    texts = [f"the garden party is on friday {PERSONNUMMER}"] * 5 + ["a quiet lunch tomorrow"] * 4
    report = cm.analyze([_msg(t, i, f"x{i}") for i, t in enumerate(texts)])
    themes = {t["terms"]: t["messages"] for t in report["unclassified_themes"]}
    assert themes == {"garden party": 5, "party friday": 5}
    assert not any("personnummer" in t or "9876" in t for t in themes)


def test_volumes_threads_domains_and_event_rows():
    messages = [cm.Message(datetime(2024, 1, 1 + d, 9, tzinfo=timezone.utc), "cancel please", f"t{d % 3}",
                           "big.example" if d < 6 else f"small{d}.example", "inbound", "email") for d in range(14)]
    messages.append(cm.Message(None, "cancel", "", "", "", "chat"))
    report = cm.analyze(messages)
    assert [w["messages"] for w in report["weekly_volume"]] == [7, 7] and report["undated_messages"] == 1
    assert report["sender_domains"] == {"big.example": 6} and report["sender_domains_below_threshold"] == 8
    assert report["threads"]["threads"] == 3 and report["threads"]["threads_with_repeat_inbound"] == 3
    assert report["channels"] == {"email": 14, "chat": 1}
    rows = cm.events_csv(report["_events"]).decode().splitlines()
    assert rows[0] == "thread,intent,time,direction" and len(rows) == 15
    assert all("cancel" not in row.lower() or "Cancellation" in row for row in rows[1:])


def test_message_limit_and_empty_input():
    with pytest.raises(cm.CommunicationsError):
        cm.analyze([])
    original = cm.MAX_MESSAGES
    try:
        cm.MAX_MESSAGES = 3
        with pytest.raises(cm.CommunicationsError):
            cm.analyze([_msg("x", i) for i in range(4)])
    finally:
        cm.MAX_MESSAGES = original


def _email(i, sender, subject, body, reply_to=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "support@shop.example"
    msg["Subject"] = subject
    msg["Date"] = (datetime(2024, 3, 4, 9, tzinfo=timezone.utc) + timedelta(hours=i)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Message-ID"] = f"<m{i}@x>"
    if reply_to:
        msg["In-Reply-To"] = reply_to
        msg["References"] = reply_to
    msg.set_content(body)
    return msg


def test_mbox_and_eml_parsing_threads_direction_and_strips_quotes(tmp_path):
    first = _email(0, "Kim <kim@customer.example>", "Cancel", "Please cancel my plan.\n\nOn Mon, Kim wrote:\n> old refund text")
    reply = _email(2, "Support <help@shop.example>", "Re: Cancel", "Done.\n> Please cancel my plan.", "<m0@x>")
    box = tmp_path / "mail.mbox"
    box.write_bytes(b"".join(b"From x@y Mon Mar  4 09:00:00 2024\n" + m.as_bytes() + b"\n" for m in (first, reply)))
    messages = list(cm.read_mbox(str(box), ["shop.example"]))
    assert [m.direction for m in messages] == ["inbound", "outbound"]
    assert messages[0].thread == messages[1].thread != ""
    assert "refund" not in messages[0].text and messages[0].timestamp.tzinfo is not None
    report = cm.analyze(messages)
    assert {i["intent"]: i["messages"] for i in report["intents"]} == {"Cancellation": 1, "outbound reply": 1}
    assert report["threads"]["first_response_seconds"]["median"] == 7200
    single = list(cm.read_eml(BytesIO(first.as_bytes())))
    assert len(single) == 1 and single[0].direction == ""


def test_csv_tickets_map_columns_and_directions():
    data = "id;created;who;subject;body\n1;2024-02-01T10:00:00Z;customer;Login;Password reset fails\n" \
           "1;2024-02-01T11:30:00+00:00;agent;Re;Try again\n2;not a date;customer;Hi;Where is my order\n"
    messages = list(cm.read_csv(StringIO(data), text_columns=["subject", "body"], time_column="created",
                                thread_column="id", direction_column="who", delimiter=";"))
    assert [m.direction for m in messages] == ["inbound", "outbound", "inbound"]
    assert messages[2].timestamp is None and messages[0].thread == messages[1].thread
    report = cm.analyze(messages)
    assert report["threads"]["first_response_seconds"]["median"] == 5400
    with pytest.raises(cm.CommunicationsError):
        list(cm.read_csv(StringIO(data), text_columns=["missing"], delimiter=";"))


def test_real_http_routes_auth_consent_taxonomy_and_saved_intent_log(tmp_path):
    code = r'''
import json, os
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.db import DATA_DIR

rows = ["ticket,created,direction,text"]
for i in range(8):
    rows += [f"T{i},2024-02-0{1 + i % 7}T09:00:00Z,inbound,Please cancel my subscription",
             f"T{i},2024-02-0{1 + i % 7}T10:00:00Z,outbound,Cancelled for you"]
body = "\n".join(rows).encode()
query = "/v1/communications/analyze?format=csv&text_columns=text&time_column=created&thread_column=ticket&direction_column=direction"
with TestClient(app) as client:
    assert client.post(query, content=body).status_code == 401
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    assert client.post(query, content=body, headers=headers).status_code == 403
    confirmed = {**headers, "x-owg-local-processing-confirmed": "true"}
    report = client.post(query, content=body, headers=confirmed)
    assert report.status_code == 200, report.text
    data = report.json()
    assert {i["intent"]: (i["messages"], i["share"]) for i in data["intents"]} == {
        "Cancellation": (8, 1.0), "outbound reply": (8, None)}
    assert "_events" not in data and "event_log" not in data and report.headers["cache-control"] == "no-store"
    assert "Please cancel" not in report.text
    assert not [p for p in os.listdir(DATA_DIR) if p.startswith("owg-comms-")]
    assert client.post(query + "&save_event_log=true", content=body, headers=confirmed).status_code == 403
    saved = client.post(query + "&save_event_log=true&name=Tickets", content=body,
                        headers={**confirmed, "x-owg-local-storage-confirmed": "true"})
    assert saved.status_code == 200 and saved.json()["event_log"]["events"] == 16, saved.text
    logs = client.get("/v1/event-logs", headers=headers).json()["logs"]
    assert logs[0]["name"] == "Tickets"
    variants = client.get("/v1/event-logs/" + logs[0]["log_id"] + "/variants", headers=headers).json()
    assert variants["variants"][0]["path"] == ["Cancellation", "outbound reply"]

    assert client.get("/v1/communications/taxonomy", headers=headers).json()["is_default"] is True
    custom = {"format": "openworkgraph.communications-taxonomy.v1", "intents": [{"name": "Churn", "terms": ["cancel"]}]}
    assert client.put("/v1/communications/taxonomy", json={"format": "bad"}, headers=headers).status_code == 422
    assert client.put("/v1/communications/taxonomy", content=b"x", headers={**headers, "Content-Type": "text/plain"}).status_code == 415
    assert client.put("/v1/communications/taxonomy", json=custom, headers=headers).status_code == 200
    again = client.post(query, content=body, headers=confirmed).json()
    assert {i["intent"]: i["messages"] for i in again["intents"]}["Churn"] == 8
    assert client.post("/v1/communications/taxonomy/reset", headers=headers).json()["is_default"] is True
    assert client.post("/v1/communications/analyze?format=csv&text_columns=nope", content=body, headers=confirmed).status_code == 422
    assert client.post("/v1/communications/analyze?format=csv", content=body, headers=confirmed).status_code == 422
    assert client.post("/v1/communications/analyze?format=mbox", content=b"\xff\xfe garbage", headers=confirmed).status_code == 422
    assert client.get("/communications-mining.js").status_code == 200
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_dashboard_client_is_wired_and_helpers_parse():
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    script = (ROOT / "dashboard/communications_mining.js").read_text(encoding="utf-8")
    assert '<script src="/communications-mining.js"></script>' in html
    assert "innerHTML" not in script and "localStorage" not in script
    assert "'X-OWG-Local-Processing-Confirmed':'true'" in script
    import shutil
    if not shutil.which("node"):
        return
    check = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/communications_mining.js','utf8'),scope);
const m=scope.window.__owgCommsModel;const plain=v=>JSON.parse(JSON.stringify(v));
assert.deepEqual(plain(m.parseTaxonomy('Refund: refund, money back\n\nChurn:cancel')),{format:'openworkgraph.communications-taxonomy.v1',intents:[{name:'Refund',terms:['refund','money back']},{name:'Churn',terms:['cancel']}]});
assert.throws(()=>m.parseTaxonomy('no colon'));assert.throws(()=>m.parseTaxonomy('A: ,'));assert.throws(()=>m.parseTaxonomy(''));
assert.equal(m.formatTaxonomy({intents:[{name:'A',terms:['x','y']}]}),'A: x, y');
assert.equal(m.detectFormat('a.EML'),'eml');assert.equal(m.detectFormat('t.csv'),'csv');assert.equal(m.detectFormat('Inbox'),'mbox');
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def _fake(prefix: str, length: int) -> str:
    """A key-shaped placeholder built at runtime, so no credential-like literal sits in the repository."""
    return prefix + ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" * 2)[:length]


@pytest.mark.parametrize("text", [
    _fake("AKIA", 16).upper(), _fake("sk-", 30), _fake("ghp_", 36),
    _fake("xoxb-", 13), _fake("sk_live_", 18), _fake("AIza", 35),
    "eyJabcdefghijk.eyJabcdefghijk.abcdefghijklm", "password=hunterhunter", "Bearer abcdefghijklmnopqrst",
    "postgres://user:secretpw@db", "-----BEGIN RSA " + "PRIVATE KEY-----\nabc\n-----END RSA " + "PRIVATE KEY-----",
    "SE45 5000 0000 0583 9825 7466", "4111 1111 1111 1111", "19811218-9876",
])
def test_redaction_gate_opens_for_every_identifier_the_redactor_handles(text):
    from shared.core.sensitive_identifiers import redact_sensitive_identifiers
    assert redact_sensitive_identifiers(text) != text, "sample must be one the redactor changes"
    assert cm._MAY_HOLD_IDENTIFIER.search(text)


def test_redaction_gate_skips_only_text_the_redactor_leaves_unchanged():
    from shared.core.sensitive_identifiers import redact_sensitive_identifiers
    for text in ("lovely weather, no ids here", "Please cancel my plan", "Where is my order? It has not arrived"):
        assert not cm._MAY_HOLD_IDENTIFIER.search(text) and redact_sensitive_identifiers(text) == text
