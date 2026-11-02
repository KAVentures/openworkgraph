"""Procure-to-pay domain pack (#370 R23), and stability of the order-to-cash pack it sits next to."""
from __future__ import annotations

import copy
from io import BytesIO
import json

import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from procure_to_pay_fixture import MAPPING, NAMES, TYPES, procure_to_pay

PACK = "procure_to_pay"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log() -> str:
    return workspace.import_log(BytesIO(json.dumps(procure_to_pay()).encode()), name="p2p",
                                source_format="ocel")["log_id"]


def _rules(report):
    return {(g["case_type"], r["rule"]["template"], r["rule"]["a"], r["rule"].get("b")): r
            for g in report["compliance"] for r in g["results"]}


def test_the_catalog_offers_both_packs():
    listed = {p["pack_id"]: p for p in packs.catalog()["packs"]}
    assert list(listed) == ["order_to_cash", PACK, "service_management"]
    assert listed[PACK]["definitions"] == {"metrics": 4, "compliance_rules": 7, "object_rules": 3,
                                          "reference_model_types": 5}


def test_the_order_to_cash_definitions_are_unchanged_for_existing_installs():
    """Installs pin a digest of their resolved definitions; adding a pack must not change it."""
    from order_to_cash_fixture import MAPPING as O2C
    pack = packs.definitions("order_to_cash", "1.0.0")
    params = {p["name"]: float(p["default"]) for p in pack["parameters"]}
    assert packs._digest(packs.resolve(pack, O2C, params)) == \
        "d07a045ce5f19ba7e60f7e7a0017a907f67bc796fe4b62aa2575a591eed12ce7"


def test_the_report_finds_every_planted_procurement_deviation():
    log = _log()
    installed = packs.install(log, PACK, {"version": "1.0.0", "mapping": copy.deepcopy(MAPPING)})
    assert installed["not_applicable"] == [] and len(installed["metric_ids"]) == 4
    report = packs.report(log, PACK)
    assert "typical procure to pay process" in report["interpretation"]
    po, rq, inv = TYPES["purchase_order"], TYPES["requisition"], TYPES["invoice"]
    rules = _rules(report)
    # Three-way match: orders 8, 18 and 28 are invoiced before their goods arrive.
    three_way = rules[(po, "precedence", NAMES["receive_goods"], NAMES["post_invoice"])]
    assert three_way["violating_cases"] == 3
    assert sorted(e["case_id"] for e in three_way["violating_case_examples"]) == ["po18", "po28", "po8"]
    assert rules[(po, "response_within", NAMES["create_po"], NAMES["receive_goods"])]["violating_cases"] == 3
    # Orders 5, 11, 17, 23 and 29 are never approved, yet received.
    assert rules[(po, "precedence", NAMES["approve_po"], NAMES["receive_goods"])]["violating_cases"] == 5
    four_eyes = rules[(po, "segregation_of_duties", NAMES["create_po"], NAMES["approve_po"])]
    assert (four_eyes["unknown_cases"], four_eyes["satisfied_cases"]) == (25, 0)
    assert rules[(rq, "precedence", NAMES["approve_requisition"], NAMES["create_po"])]["violating_cases"] == 4
    # Invoice 20 is never paid; invoice 12 is paid twice.
    assert rules[(inv, "response_within", NAMES["post_invoice"], NAMES["pay_invoice"])]["violating_cases"] == 1
    once = rules[(inv, "existence", NAMES["pay_invoice"], None)]
    assert once["violating_cases"] == 2 and sorted(e["case_id"] for e in once["violating_case_examples"]) == [
        "inv12", "inv20"]
    objects = {r["rule"]["template"] + ":" + r["rule"]["activity"]: r for r in report["object_rules"]["results"]
               if "activity" in r["rule"]}
    # The invoice posted without any purchase order, and the three invoices ahead of their goods receipt.
    assert objects["event_cardinality:" + NAMES["post_invoice"]]["violating"] == 1
    assert objects["synchronization:" + NAMES["post_invoice"]]["violating"] == 3
    model = report["object_model"]
    assert model["summary"]["fitting_objects"] == 147 and model["summary"]["objects"] == 152
    assert model["arcs"]["issues"] == {"missing_object": 1, "several_objects_on_single_arc": 0, "type_without_arc": 0}
    lifecycles = {t["object_type"]: t for t in model["lifecycles"]}
    assert lifecycles[po]["missing_tokens_by_activity"] == {NAMES["post_invoice"]: 3}
    assert lifecycles[inv]["fitting_objects"] == 29
    metrics = {m["name"]: m for m in report["metrics"]}
    assert metrics["p2p_order_to_receipt"]["median"] == 7 * 86400
    assert metrics["p2p_invoice_to_payment"]["coverage"]["cases_with_value"] == 30


def test_a_log_without_requisitions_or_receipts_still_gets_the_core_checks():
    log = _log()
    mapping = copy.deepcopy(MAPPING)
    for role in ("requisition", "goods_receipt", "payment"):
        del mapping["object_types"][role]
    for role in ("create_requisition", "approve_requisition", "approve_po", "change_po"):
        del mapping["activities"][role]
    installed = packs.install(log, PACK, {"version": "1.0.0", "mapping": mapping})
    skipped = {(n["section"], n["unmapped_role"]) for n in installed["not_applicable"]}
    assert ("metrics", "requisition") in skipped and ("compliance", "approve_po") in skipped
    assert ("reference_model", "requisition") in skipped and ("reference_model", "goods_receipt") in skipped
    report = packs.report(log, PACK)
    po = TYPES["purchase_order"]
    assert _rules(report)[(po, "precedence", NAMES["receive_goods"], NAMES["post_invoice"])]["violating_cases"] == 3
    assert {t["object_type"] for t in report["object_model"]["lifecycles"]} == {po, TYPES["invoice"]}


def test_both_packs_install_side_by_side_without_sharing_metrics():
    from order_to_cash_fixture import order_to_cash, MAPPING as O2C
    doc = procure_to_pay()
    o2c = json.loads(json.dumps(order_to_cash()).replace('"so', '"x-so').replace('"dl', '"x-dl')
                     .replace('"bd', '"x-bd').replace('"pm', '"x-pm'))     # keep identifiers unique
    doc["objectTypes"] += o2c["objectTypes"]
    doc["objects"] += o2c["objects"]
    doc["events"] += o2c["events"]
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()), name="both", source_format="ocel")["log_id"]
    packs.install(log, PACK, {"version": "1.0.0", "mapping": MAPPING})
    packs.install(log, "order_to_cash", {"version": "1.0.0", "mapping": O2C})
    assert [i["pack_id"] for i in packs.installed(log)["installs"]] == ["order_to_cash", PACK]
    assert packs.uninstall(log, PACK)["metrics_removed"] == 4
    assert packs.report(log, "order_to_cash")["object_model"]["summary"]["fitting_objects"] == 115
