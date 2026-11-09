"""Pack metadata and metric ownership must commit together, including failures."""
import copy
import json
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from order_to_cash_fixture import MAPPING, order_to_cash


@pytest.fixture
def log_id(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    return workspace.import_log(BytesIO(json.dumps(order_to_cash()).encode()),
        name="Synthetic atomicity", source_format="ocel")["log_id"]


def install(log_id, **extra):
    return packs.install(log_id, "order_to_cash", {"version": "1.0.0", "mapping": copy.deepcopy(MAPPING), **extra})


def snapshot(log_id):
    return packs.installed(log_id)["installs"], query.list_metrics(log_id)["metrics"]


def manual_metrics(log_id, count):
    for n in range(count):
        query.save_metric(log_id, {"metric": {"name": f"manual_{n}", "kind": "activity_count",
                                              "activity": MAPPING["activities"]["create_order"]}})


def test_capacity_failure_leaves_no_partial_first_install(log_id):
    manual_metrics(log_id, query.MAX_SAVED - 2)
    before = snapshot(log_id)
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most"):
        install(log_id)
    assert snapshot(log_id) == before


def test_reconfiguration_at_capacity_replaces_only_owned_metrics(log_id):
    saved = install(log_id)
    manual_metrics(log_id, query.MAX_SAVED - len(saved["metric_ids"]))
    manual = [m for m in query.list_metrics(log_id)["metrics"] if m["metric_id"] not in saved["metric_ids"]]
    updated = install(log_id, parameters={"payment_terms_days": 45})
    assert updated["change"] == "reconfigured"
    assert len(query.list_metrics(log_id)["metrics"]) == query.MAX_SAVED
    assert [m for m in query.list_metrics(log_id)["metrics"] if m["metric_id"] not in updated["metric_ids"]] == manual


@pytest.mark.parametrize("previous", [False, True])
def test_mid_metric_failure_restores_exact_prior_state(log_id, monkeypatch, previous):
    if previous:
        install(log_id)
    manual_metrics(log_id, 1)
    before = snapshot(log_id)
    original = packs._insert_pack_metric
    count = 0

    def broken(*args):
        nonlocal count
        count += 1
        result = original(*args)
        if count == 2:
            raise OSError("synthetic storage interruption")
        return result

    monkeypatch.setattr(packs, "_insert_pack_metric", broken)
    with pytest.raises(OSError, match="synthetic"):
        install(log_id, parameters={"payment_terms_days": 45})
    assert count == 2
    assert snapshot(log_id) == before
    monkeypatch.setattr(packs, "_insert_pack_metric", original)
    install(log_id)  # rollback released the transaction and connection


class BrokenConnection:
    def __init__(self, conn, prefix):
        self.conn, self.prefix = conn, prefix

    def execute(self, sql, *args):
        result = self.conn.execute(sql, *args)
        if sql.startswith(self.prefix):
            raise OSError("synthetic post-statement failure")
        return result

    def close(self):
        self.conn.close()


@pytest.mark.parametrize("previous", [False, True])
def test_install_record_failure_restores_metrics_and_metadata(log_id, monkeypatch, previous):
    if previous:
        install(log_id)
    before = snapshot(log_id)
    original = workspace._connect
    with monkeypatch.context() as patch:
        patch.setattr(workspace, "_connect", lambda: BrokenConnection(original(), "INSERT INTO domain_pack_installs"))
        with pytest.raises(OSError, match="post-statement"):
            install(log_id, parameters={"payment_terms_days": 45})
    assert snapshot(log_id) == before


def test_uninstall_failure_restores_install_and_all_metrics(log_id, monkeypatch):
    saved = install(log_id)
    manual_metrics(log_id, 1)
    before = snapshot(log_id)
    original = workspace._connect
    with monkeypatch.context() as patch:
        patch.setattr(workspace, "_connect", lambda: BrokenConnection(original(), "DELETE FROM metric_definitions"))
        with pytest.raises(OSError, match="post-statement"):
            packs.uninstall(log_id, "order_to_cash")
    assert snapshot(log_id) == before
    assert packs.report(log_id, "order_to_cash")["pack"]["definitions_sha256"] == saved["definitions_sha256"]
    assert packs.uninstall(log_id, "order_to_cash")["metrics_removed"] == 4
    assert len(query.list_metrics(log_id)["metrics"]) == 1


def test_concurrent_installs_have_one_coherent_owner_set(log_id):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: install(log_id), range(2)))
    records, metrics = snapshot(log_id)
    assert sorted(r["change"] for r in results) == ["installed", "reconfigured"]
    assert len(records) == 1 and len(records[0]["history"]) == 1
    assert {m["metric_id"] for m in metrics} == set(records[0]["metric_ids"])
    assert len(metrics) == 4


def test_invalid_replacement_metric_rolls_back_prior_install(log_id, monkeypatch):
    install(log_id)
    before = snapshot(log_id)
    original = packs.resolve

    def invalid(*args):
        definitions = original(*args)
        definitions["metrics"][1]["metric"]["kind"] = "unrecognized_metric"
        return definitions

    monkeypatch.setattr(packs, "resolve", invalid)
    with pytest.raises(workspace.EventLogWorkspaceError, match="invalid case metric"):
        install(log_id)
    assert snapshot(log_id) == before


def test_uninstall_tolerates_missing_owned_metric_without_deleting_manual(log_id):
    saved = install(log_id)
    query.delete_metric(log_id, saved["metric_ids"][0])
    manual_metrics(log_id, 1)
    removed = packs.uninstall(log_id, "order_to_cash")
    assert removed["metrics_removed"] == 3
    assert packs.installed(log_id)["installs"] == []
    assert [m["metric"]["name"] for m in query.list_metrics(log_id)["metrics"]] == ["manual_0"]
