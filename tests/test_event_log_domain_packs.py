"""Versioned order-to-cash domain pack (#370 R23)."""
from __future__ import annotations

import copy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from order_to_cash_fixture import MAPPING, NAMES, TYPES, order_to_cash

ROOT = Path(__file__).resolve().parents[1]
PACK = "order_to_cash"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(doc=None) -> str:
    return workspace.import_log(BytesIO(json.dumps(doc or order_to_cash()).encode()), name="o2c",
                                source_format="ocel")["log_id"]


def _install(log, **extra):
    return packs.install(log, PACK, {"version": "1.0.0", "mapping": copy.deepcopy(MAPPING), **extra})


def _results(report):
    rows = {}
    for group in report["compliance"]:
        for r in group["results"]:
            rows[(group["case_type"], r["rule"]["template"], r["rule"]["a"], r["rule"].get("b"))] = r
    return rows


def test_the_catalog_lists_the_pack_roles_and_definitions():
    pack = packs.catalog()["packs"][0]
    assert pack["pack_id"] == PACK and pack["versions"] == ["1.0.0"] and pack["latest_version"] == "1.0.0"
    assert {r["role"] for r in pack["object_types"] if r["required"]} == {"order", "item", "delivery", "invoice"}
    assert pack["definitions"] == {"metrics": 4, "compliance_rules": 6, "object_rules": 4, "reference_model_types": 5}
    assert packs.definitions(PACK)["version"] == "1.0.0"


def test_the_report_finds_every_planted_deviation_through_the_mapping():
    log = _log()
    installed = _install(log)
    assert installed["change"] == "installed" and installed["not_applicable"] == [] and len(installed["metric_ids"]) == 4
    report = packs.report(log, PACK)
    assert report["pack"]["version"] == "1.0.0" and report["source_system_validated"] is False
    metrics = {m["name"]: m for m in report["metrics"]}
    # Invoices follow orders by 3 days, except orders 3 and 13 at 20 days.
    assert metrics["o2c_order_to_invoice"]["median"] == 3 * 86400 and metrics["o2c_order_to_invoice"]["cases"] == 20
    # Orders 0, 4, 8, 12 and 16 are changed once.
    assert metrics["o2c_order_changes"]["mean"] == 5 / 20
    # Invoice 9 is never paid; the others 22 days after invoicing.
    pay = metrics["o2c_invoice_to_payment"]
    assert pay["coverage"]["cases_with_value"] == 19 and pay["coverage"]["cases_without_to_activity"] == 1
    rules = _results(report)
    order, item, invoice = TYPES["order"], TYPES["item"], TYPES["invoice"]
    late = rules[(order, "response_within", NAMES["create_order"], NAMES["create_invoice"])]
    assert (late["activated_cases"], late["violating_cases"]) == (20, 2)
    assert sorted(e["case_id"] for e in late["violating_case_examples"]) == ["so13", "so3"]
    unpicked = rules[(item, "precedence", NAMES["pick_item"], NAMES["ship"])]
    assert unpicked["violating_cases"] == 2
    terms = rules[(invoice, "response_within", NAMES["create_invoice"], NAMES["receive_payment"])]
    assert terms["violating_cases"] == 1 and terms["violating_case_examples"][0]["case_id"] == "bd9"
    # OCEL keeps no event resources, so four-eyes cannot be judged: unknown, never satisfied.
    four_eyes = rules[(order, "segregation_of_duties", NAMES["create_order"], NAMES["approve_credit"])]
    assert four_eyes["violating_cases"] == 0 and four_eyes["unknown_cases"] == 6 and four_eyes["satisfied_cases"] == 0
    sync = next(r for r in report["object_rules"]["results"] if r["rule"]["template"] == "synchronization")
    assert sync["violating"] == 2
    model = report["object_model"]
    assert model["summary"] == {"objects": 118, "replayed_objects": 118, "fitting_objects": 115,
                                "fitting_object_share": 115 / 118, "events_checked": 127, "fitting_events": 127,
                                "complete": True}
    lifecycles = {t["object_type"]: t for t in model["lifecycles"]}
    assert lifecycles[item]["missing_tokens_by_activity"] == {NAMES["ship"]: 2}
    assert lifecycles[invoice]["fitting_objects"] == 19


def test_existence_rule_counts_the_unpaid_invoice():
    log = _log()
    _install(log)
    group = next(g for g in packs.report(log, PACK)["compliance"] if g["case_type"] == TYPES["invoice"])
    paid = next(r for r in group["results"] if r["rule"]["template"] == "existence")
    assert (paid["activated_cases"], paid["violating_cases"]) == (20, 1)


def test_unmapped_optional_roles_are_listed_not_applicable():
    log = _log()
    mapping = copy.deepcopy(MAPPING)
    for role in ("approve_credit", "change_order"):
        del mapping["activities"][role]
    del mapping["object_types"]["payment"]
    installed = packs.install(log, PACK, {"version": "1.0.0", "mapping": mapping})
    assert installed["not_applicable"] == [
        {"section": "metrics", "index": 1, "unmapped_role": "change_order"},
        {"section": "compliance", "index": 2, "unmapped_role": "approve_credit"},
        {"section": "reference_model", "index": 4, "unmapped_role": "payment"}]
    assert len(installed["metric_ids"]) == 3
    report = packs.report(log, PACK)
    order = next(t for t in report["object_model"]["lifecycles"] if t["object_type"] == TYPES["order"])
    # The optional steps leave the order model; their events are unmodelled for orders that had them.
    assert order["unmodelled_activities"] == {NAMES["approve_credit"]: 6, NAMES["change_order"]: 5}
    assert TYPES["payment"] not in {t["object_type"] for t in report["object_model"]["lifecycles"]}


def test_parameters_change_the_resolved_rules():
    log = _log()
    installed = _install(log, parameters={"payment_terms_days": 20})
    assert installed["parameters"] == {"invoice_within_days": 14.0, "payment_terms_days": 20.0}
    terms = _results(packs.report(log, PACK))[(TYPES["invoice"], "response_within", NAMES["create_invoice"],
                                               NAMES["receive_payment"])]
    # Payments come 22 days after invoicing, except 5 days for the late invoices 3 and 13; invoice 9 is unpaid.
    assert terms["rule"]["seconds"] == 20 * 86400 and terms["violating_cases"] == 17 + 1


def test_upgrades_keep_history_reconfigure_in_place_and_refuse_downgrades(monkeypatch):
    newer = copy.deepcopy(packs.ORDER_TO_CASH_1_0_0)
    newer["version"] = "1.1.0"
    newer["parameters"][1]["default"] = 45
    monkeypatch.setitem(packs.PACKS, PACK, {"1.0.0": packs.ORDER_TO_CASH_1_0_0, "1.1.0": newer})
    log = _log()
    first = _install(log)
    status = packs.installed(log)
    assert status["installs"][0]["upgrade_available"] is True and status["names_truncated"] is False
    assert set(status["log_object_types"]) == set(TYPES.values()) and set(status["log_activities"]) == set(NAMES.values())
    upgraded = packs.install(log, PACK, {"version": "1.1.0", "mapping": MAPPING})
    assert upgraded["change"] == "upgraded" and upgraded["previous_version"] == "1.0.0"
    assert upgraded["history"] == [{"version": "1.0.0", "installed_at": first["installed_at"],
                                    "definitions_sha256": first["definitions_sha256"]}]
    assert upgraded["definitions_sha256"] != first["definitions_sha256"] and upgraded["parameters"]["payment_terms_days"] == 45
    # The pack's metrics are replaced, never duplicated.
    assert sorted(m["metric"]["name"] for m in query.list_metrics(log)["metrics"]) == sorted(
        d["metric"]["name"] for d in newer["metrics"])
    assert set(upgraded["metric_ids"]).isdisjoint(first["metric_ids"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="downgrading to 1.0.0 is refused"):
        _install(log)
    again = packs.install(log, PACK, {"version": "1.1.0", "mapping": MAPPING, "parameters": {"payment_terms_days": 60}})
    assert again["change"] == "reconfigured" and len(again["history"]) == 2
    status = packs.installed(log)["installs"][0]
    assert status["version"] == "1.1.0" and status["upgrade_available"] is False


def test_a_users_own_metric_is_never_overwritten_and_uninstall_removes_only_pack_metrics():
    log = _log()
    query.save_metric(log, {"metric": {"name": "o2c_order_changes", "kind": "activity_count", "activity": NAMES["ship"]}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="o2c_order_changes exists and is not this pack's"):
        _install(log)
    assert packs.installed(log)["installs"] == []
    query.delete_metric(log, query.list_metrics(log)["metrics"][0]["metric_id"])
    query.save_metric(log, {"metric": {"name": "mine", "kind": "activity_count", "activity": NAMES["ship"]}})
    _install(log)
    assert len(query.list_metrics(log)["metrics"]) == 5
    removed = packs.uninstall(log, PACK)
    assert removed == {"uninstalled": True, "pack_id": PACK, "version": "1.0.0", "metrics_removed": 4}
    assert [m["metric"]["name"] for m in query.list_metrics(log)["metrics"]] == ["mine"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="not installed"):
        packs.uninstall(log, PACK)
    with pytest.raises(workspace.EventLogWorkspaceError, match="install this domain pack"):
        packs.report(log, PACK)


def test_changed_definitions_under_the_same_version_are_detected(monkeypatch):
    log = _log()
    _install(log)
    edited = copy.deepcopy(packs.ORDER_TO_CASH_1_0_0)
    edited["compliance"].pop()
    monkeypatch.setitem(packs.PACKS, PACK, {"1.0.0": edited})
    with pytest.raises(workspace.EventLogWorkspaceError, match="no longer match their digest"):
        packs.report(log, PACK)


def test_deleting_the_log_deletes_its_install():
    log = _log()
    _install(log)
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM domain_pack_installs WHERE log_id = ?", [log]).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(version="9.9.9"), "no version 9.9.9"),
    (lambda s: s["mapping"]["object_types"].pop("order"), "required role order"),
    (lambda s: s["mapping"]["activities"].pop("ship"), "required role ship"),
    (lambda s: s["mapping"]["activities"].update(audit="Post Goods Issue"), "no activity role audit"),
    (lambda s: s["mapping"]["activities"].update(change_order=NAMES["ship"]), "same name"),
    (lambda s: s["mapping"]["object_types"].update(order="Order"), "not an object type of this log"),
    (lambda s: s["mapping"]["activities"].update(ship="Ship it"), "not an activity of this log"),
    (lambda s: s.update(parameters={"payment_terms_days": 0}), "from 1 to 365"),
    (lambda s: s.update(parameters={"discount": 2}), "no parameter discount"),
    (lambda s: s.update(extra=True), "extra"),
])
def test_invalid_installs_are_refused(change, message):
    log = _log()
    spec = {"version": "1.0.0", "mapping": copy.deepcopy(MAPPING)}
    change(spec)
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        packs.install(log, PACK, spec)
    assert packs.installed(log)["installs"] == [] and query.list_metrics(log)["metrics"] == []


def test_unknown_packs_are_refused():
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown domain pack"):
        packs.definitions("hire_to_retire")


def test_real_http_routes_require_a_dashboard_session(tmp_path):
    code = r'''
import json, sys
sys.path.insert(0, "tests")
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from order_to_cash_fixture import order_to_cash, MAPPING
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    made = client.post("/v1/event-logs/import?format=ocel&name=o", content=json.dumps(order_to_cash()).encode(),
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    log = "/v1/event-logs/" + made.json()["log_id"]
    assert client.get("/v1/event-log-domain-packs", headers=bearer).status_code in (401, 403)
    listed = client.get("/v1/event-log-domain-packs", headers=headers)
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store", listed.text
    assert client.get("/v1/event-log-domain-packs/order_to_cash?version=1.0.0", headers=headers).json()["version"] == "1.0.0"
    assert client.get("/v1/event-log-domain-packs/Bad-Id", headers=headers).status_code == 422
    spec = {"version": "1.0.0", "mapping": MAPPING}
    assert client.post(log + "/domain-packs/order_to_cash", json=spec, headers=bearer).status_code in (401, 403)
    done = client.post(log + "/domain-packs/order_to_cash", json=spec, headers=headers)
    assert done.status_code == 200 and done.json()["change"] == "installed", done.text
    assert client.get(log + "/domain-packs", headers=headers).json()["installs"][0]["version"] == "1.0.0"
    report = client.get(log + "/domain-packs/order_to_cash/report", headers=headers)
    assert report.status_code == 200 and report.headers["cache-control"] == "no-store", report.text
    assert report.json()["object_model"]["summary"]["fitting_objects"] == 115
    assert client.get(log + "/domain-packs/order_to_cash/report", headers=bearer).status_code in (401, 403)
    assert client.post(log + "/domain-packs/order_to_cash", json={"version": "1.0.0"}, headers=headers).status_code == 422
    assert client.delete(log + "/domain-packs/order_to_cash", headers=bearer).status_code in (401, 403)
    assert client.delete(log + "/domain-packs/order_to_cash", headers=headers).json()["metrics_removed"] == 4
    assert client.get(log + "/domain-packs/order_to_cash/report", headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
