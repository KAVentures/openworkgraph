"""SAP S/4HANA order-to-cash extraction: paging, retries, grants, checkpoints, pseudonyms (#370 R3)."""
from datetime import datetime, timedelta, timezone
from email.message import Message
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from shared import sap_odata_o2c as sap

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://my-erp.s4hana.example/sap/opu/odata/sap"
T0 = datetime(2026, 3, 2, tzinfo=timezone.utc)


def ms(moment):
    return f"/Date({int(moment.timestamp() * 1000)})/"


def documents(extra_order=False):
    orders = [{"SalesOrder": f"SO-SECRET-{i}", "SalesOrderType": "OR", "CreationDate": ms(T0 + timedelta(days=i)),
               "LastChangeDateTime": ms(T0 + timedelta(days=i)) .replace(")/", "+0000)/"),
               "OverallSDProcessStatus": "C", "CreatedByUser": "ALICE.SMITH", "TotalNetAmount": "1200.00",
               "TransactionCurrency": "EUR", "to_Item": {"results": [{"SalesOrderItem": "10"}, {"SalesOrderItem": "20"}]}}
              for i in range(3 + extra_order)]
    deliveries = [{"DeliveryDocument": f"DL-{i}", "CreationDate": ms(T0 + timedelta(days=i + 1)), "CreationTime": "PT09H30M00S",
                   "PickingDate": ms(T0 + timedelta(days=i + 1)), "PickingTime": "PT11H00M00S",
                   "ActualGoodsMovementDate": ms(T0 + timedelta(days=i + 2)), "ActualGoodsMovementTime": "PT14H00M00S",
                   "LastChangeDate": ms(T0 + timedelta(days=i + 2)), "OverallGoodsMovementStatus": "C", "CreatedByUser": "BOB",
                   "to_DeliveryDocumentItem": {"results": [{"ReferenceSDDocument": f"SO-SECRET-{i}", "ReferenceSDDocumentItem": "10"},
                                                           {"ReferenceSDDocument": f"SO-SECRET-{i}", "ReferenceSDDocumentItem": "20"}]}}
                  for i in range(2)]
    billing = [{"BillingDocument": f"BD-{i}", "CreationDate": ms(T0 + timedelta(days=i + 3)), "CreationTime": "PT08H00M00S",
                "LastChangeDateTime": ms(T0 + timedelta(days=i + 4)).replace(")/", "+0000)/"),
                "BillingDocumentIsCancelled": i == 1, "CreatedByUser": "CAROL", "TotalNetAmount": "1200.00",
                "TransactionCurrency": "EUR",
                "to_Item": {"results": [{"ReferenceSDDocument": f"DL-{i}", "SalesDocument": f"SO-SECRET-{i}"}]}}
               for i in range(2)]
    return {"A_SalesOrder": orders, "A_OutbDeliveryHeader": deliveries, "A_BillingDocument": billing}


class FakeSap:
    def __init__(self, page_size=2, fail=None, metadata=b"<edmx>v1</edmx>", extra_order=False):
        self.page_size, self.fail, self.metadata, self.calls = page_size, list(fail or []), metadata, []
        self.data = documents(extra_order)

    def __call__(self, url, raw=False):
        self.calls.append(url)
        if self.fail:
            code = self.fail.pop(0)
            headers = Message()
            headers["Retry-After"] = "1"
            raise urllib.error.HTTPError(url, code, "x", headers, None)
        parts = urlsplit(url)
        if parts.path.endswith("$metadata"):
            return self.metadata
        entity = parts.path.rsplit("/", 1)[1]
        query = parse_qs(parts.query)
        skip = int(query.get("$skiptoken", ["0"])[0])
        rows = self.data[entity][skip:skip + self.page_size]
        body = {"results": rows}
        if skip + self.page_size < len(self.data[entity]):
            body["__next"] = f"{BASE}/x/{entity}?$skiptoken={skip + self.page_size}"
        return {"d": body}


def test_planned_requests_are_bounded_selects_with_change_filters():
    [orders, deliveries, billing] = sap.ENTITY_SETS
    url = sap.first_url(BASE, orders, T0, include_amounts=False)
    assert url.startswith(BASE + "/API_SALES_ORDER_SRV/A_SalesOrder?")
    assert "TotalNetAmount" not in url and "SoldToParty" not in url and "$top=500" in url
    from urllib.parse import unquote
    assert "LastChangeDateTime ge datetimeoffset'2026-03-02T00:00:00Z'" in unquote(url)
    assert "LastChangeDate ge datetime'2026-03-02T00:00:00'" in unquote(sap.first_url(BASE, deliveries, T0, include_amounts=False))
    assert "$expand=to_Item" in sap.first_url(BASE, billing, T0, include_amounts=True)
    for bad in ("http://x/sap", "https://user:pw@x/sap", "https://x/sap?a=1", "ftp://x"):
        with pytest.raises(sap.SapError):
            sap.validate_base(bad)


def test_odata_dates_times_and_paging_rules():
    assert sap.parse_date("/Date(1700000000000)/") == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert sap.parse_date("/Date(1700000000000+0000)/").year == 2023 and sap.parse_date("2023-01-01") is None
    moment, precision = sap.with_time(sap.parse_date("/Date(0)/"), "PT09H30M05S")
    assert moment.hour == 9 and moment.second == 5 and precision == "second"
    assert sap.with_time(sap.parse_date("/Date(0)/"), None)[1] == "day"
    assert sap.page({"d": {"results": [], "__next": "/sap/opu/odata/sap/x?$skiptoken=1"}}, BASE)[1].startswith("https://my-erp")
    with pytest.raises(sap.SapError, match="different host"):
        sap.page({"d": {"results": [], "__next": "https://evil.example/x"}}, BASE)
    with pytest.raises(sap.SapError):
        sap.page({"error": {}}, BASE)


def test_projection_links_documents_and_pseudonymizes_everything_identifying():
    extracted = sap.extract(BASE, FakeSap(), T0)
    assert extracted["counts"] == {"sales_orders": 3, "deliveries": 2, "billing_documents": 2}
    document = sap.project(extracted["records"], b"k" * 32)
    text = json.dumps(document)
    for secret in ("SO-SECRET", "ALICE", "BOB", "CAROL", "DL-0", "BD-1", "1200.00"):
        assert secret not in text
    types = {o["type"] for o in document["objects"]}
    assert types == {"sales_order", "sales_order_item", "delivery", "billing_document"}
    activities = sorted({e["type"] for e in document["events"]})
    assert activities == ["Cancel billing document", "Create billing document", "Create delivery",
                          "Create sales order", "Pick items", "Post goods issue"]
    delivery = next(o for o in document["objects"] if o["type"] == "delivery")
    assert sorted(r["qualifier"] for r in delivery["relationships"]) == ["carries", "carries", "fulfils"]
    created = [e for e in document["events"] if e["type"] == "Create sales order"]
    shipped_items = {r["objectId"] for e in document["events"] if e["type"] == "Post goods issue"
                     for r in e["relationships"] if r["qualifier"] == "item"}
    ordered_items = {r["objectId"] for e in created for r in e["relationships"] if r["qualifier"] == "item"}
    assert shipped_items and shipped_items <= ordered_items
    billed = next(e for e in document["events"] if e["type"] == "Create billing document")
    assert sorted(r["qualifier"] for r in billed["relationships"]) == ["delivery", "invoice", "order"]
    assert sap.project(extracted["records"], b"k" * 32) == document
    assert sap.project(extracted["records"], b"j" * 32) != document
    with_amounts = sap.project(extracted["records"], b"k" * 32, include_amounts=True)
    assert "1200.00" in json.dumps(with_amounts)


@pytest.fixture
def sync(tmp_path, monkeypatch):
    from server import event_log_workspace as workspace, sap_o2c_sync as module
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    module.configure({"service_root": BASE, "credential_env": "OWG_SAP_TOKEN", "overlap_hours": 24, "initial_days": 30})
    return module, workspace


def test_runs_are_incremental_overlap_safe_and_checkpointed(sync, tmp_path):
    module, workspace = sync
    fake = FakeSap()
    first = module.run(fetch=fake, sleep=lambda s: None, now=T0 + timedelta(days=10))
    assert first["outcome"] == "succeeded" and first["events_added"] > 0 and first["pages"] == 4
    assert first["window_since"] == (T0 + timedelta(days=10) - timedelta(days=30)).isoformat()
    log_id = first["log_id"]
    second = module.run(fetch=FakeSap(extra_order=True), sleep=lambda s: None, now=T0 + timedelta(days=11))
    assert second["window_since"] == (T0 + timedelta(days=10) - timedelta(hours=24)).isoformat()
    assert second["log_id"] == log_id and second["events_added"] == 1, second
    assert second["duplicates_skipped"] == first["events_added"]
    types = workspace.object_types(log_id)
    assert {t["object_type"] for t in types["object_types"]} == {"sales_order", "sales_order_item", "delivery",
                                                                  "billing_document"}
    if os.name == "posix":
        assert (tmp_path / "sap_o2c_source.json").stat().st_mode & 0o777 == 0o600
    assert "SO-SECRET" not in (tmp_path / "sap_o2c_source.json").read_text()


def test_rate_limits_are_retried_and_expired_grants_leave_the_checkpoint(sync):
    module, _ = sync
    slept = []
    ok = module.run(fetch=FakeSap(fail=[429, 503]), sleep=slept.append, now=T0 + timedelta(days=10))
    assert ok["retries"] == 2 and slept and all(s <= 61 for s in slept)
    before = module.status()["checkpoint"]
    with pytest.raises(module.SyncError) as err:
        module.run(fetch=FakeSap(fail=[401]), sleep=lambda s: None, now=T0 + timedelta(days=12))
    assert err.value.code == "grant_expired_or_insufficient"
    state = module.status()
    assert state["checkpoint"] == before and state["last_run"]["checkpoint_unchanged"] is True
    with pytest.raises(module.SyncError) as err:
        module.run(fetch=FakeSap(fail=[503] * 4), sleep=lambda s: None, now=T0 + timedelta(days=12))
    assert err.value.code == "source_error" and module.status()["checkpoint"] == before


def test_schema_changes_are_flagged_and_disconnect_keeps_the_log(sync):
    module, workspace = sync
    module.run(fetch=FakeSap(), sleep=lambda s: None, now=T0 + timedelta(days=10))
    changed = module.run(fetch=FakeSap(metadata=b"<edmx>v2</edmx>"), sleep=lambda s: None, now=T0 + timedelta(days=11))
    assert len(changed["schema_changed_services"]) == 3
    log_id = changed["log_id"]
    with pytest.raises(module.SyncError):
        module.configure({"service_root": "https://other.example/sap/opu/odata/sap"})
    out = module.disconnect()
    assert out["event_log_kept"] == log_id and module.status() == {"configured": False, "extraction_by_default": False}
    assert any(l["log_id"] == log_id for l in workspace.list_logs()["logs"])


def test_configuration_is_validated_and_credentials_come_from_the_environment(sync, monkeypatch):
    module, _ = sync
    for bad in ({"service_root": BASE, "credential_env": "PATH"}, {"service_root": BASE, "overlap_hours": 500},
                {"service_root": BASE, "auth_type": "cookie"}, {"service_root": BASE, "token": "x"}):
        with pytest.raises(module.SyncError):
            module.configure(bad)
    monkeypatch.delenv("OWG_SAP_TOKEN", raising=False)
    with pytest.raises(module.SyncError) as err:
        module.run(now=T0)
    assert err.value.code == "missing_credential"
    assert module.status()["credential_stored"] is False


def test_routes_require_the_dashboard_and_explicit_confirmation(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get("/v1/sources/sap-o2c", headers=bearer).status_code == 403
    assert client.get("/v1/sources/sap-o2c", headers=owner).json()["configured"] is False
    made = client.put("/v1/sources/sap-o2c", headers=owner, json={"service_root": "https://erp.example/sap/opu/odata/sap"})
    assert made.status_code == 200 and made.json()["credential_stored"] is False, made.text
    assert client.post("/v1/sources/sap-o2c/run", headers=owner).status_code == 403
    missing = client.post("/v1/sources/sap-o2c/run", headers={**owner, "x-owg-source-read-confirmed": "true"})
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "missing_credential"
    assert client.post("/v1/sources/sap-o2c/disconnect", headers=owner).json()["disconnected"] is True
'''
    env = os.environ.copy()
    env.pop("OWG_SAP_TOKEN", None)
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
