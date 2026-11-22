"""Optimal alignments of an event log on an imported PNML Petri net."""
from __future__ import annotations

import pytest

from server import event_log_alignments as al
from server import event_log_cases as cases
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError
from tests.test_event_log_petri_replay import _dirs, _import, _sequence_net  # noqa: F401  (fixture reuse)


def _moves(variant):
    return [(s["move"], s["log"] or s["model"]) for s in variant["alignment"]]


def test_discovered_net_aligns_every_case_at_zero_cost():
    traces = [["Register", "Check", "Pay", "Close"]] * 10 + [["Register", "Pay", "Check", "Close"]] * 6 \
        + [["Register", "Check", "Close"]] * 4
    log = _import(traces)
    xml, _ = pnml.discovered_pnml(log, noise=0.0)
    result = al.alignments(log, xml.encode())
    assert result["cases"] == 20 and result["fitness"]["log"] == 1.0
    assert result["fitness"]["fitting_cases"] == 20 and result["non_fitting_variants"] == []
    assert result["deviation_cost"]["total"] == 0 and result["unaligned"] == {"variants": 0, "cases": 0}


def test_skipped_extra_and_swapped_steps_are_named_exactly():
    log = _import([["A", "B", "C"]] * 6 + [["A", "C"]] * 3 + [["A", "B", "X", "C"]] * 2 + [["A", "C", "B"]])
    result = al.alignments(log, _sequence_net(["A", "B", "C"]))
    assert result["net"]["shortest_run_visible_steps"] == 3
    assert result["fitness"]["fitting_cases"] == 6
    by_path = {tuple(v["path"]): v for v in result["non_fitting_variants"]}
    skipped = by_path[("A", "C")]
    assert skipped["cost"] == 1 and skipped["fitness"] == round(1 - 1 / 5, 4)
    assert _moves(skipped) == [("sync", "A"), ("model", "B"), ("sync", "C")]
    assert cases.cases(log, {"variant_id": skipped["variant_id"]})["selected_cases"] == skipped["cases"]
    extra = by_path[("A", "B", "X", "C")]
    assert _moves(extra) == [("sync", "A"), ("sync", "B"), ("log", "X"), ("sync", "C")]
    swapped = by_path[("A", "C", "B")]
    # Two explanations cost 2 (skip B then add B, or add C then skip C); one is returned.
    assert swapped["cost"] == 2 and sorted(m for m, _ in _moves(swapped)) == ["log", "model", "sync", "sync"]
    model_b = next(m for m in result["model_moves"] if m["activity"] == "B")
    assert model_b["cases"] >= 3
    assert result["log_moves"][0] == {"activity": "X", "cases": 2, "times": 2}
    assert result["activities_not_in_net"] == ["X"]
    total_cost = 3 * 1 + 2 * 1 + 1 * 2
    worst = 6 * 6 + 3 * 5 + 2 * 7 + 1 * 6
    assert result["deviation_cost"]["total"] == total_cost
    assert result["fitness"]["log"] == round(1 - total_cost / worst, 4)


def test_silent_transitions_are_free_and_hidden():
    log = _import([["A", "B", "C"]] * 5 + [["A", "C"]] * 5)
    result = al.alignments(log, _sequence_net(["A", "B", "C"], final=False, silent_skip=1))
    assert result["fitness"]["log"] == 1.0 and result["fitness"]["fitting_cases"] == 10
    # The shortest run takes the silent skip, so only A and C are visible steps.
    assert result["net"]["shortest_run_visible_steps"] == 2


def test_search_limit_reports_unaligned_variants(monkeypatch):
    log = _import([["A", "B", "C"]] * 3 + [["C", "B", "A", "A", "B"]])
    monkeypatch.setattr(al, "MAX_STATES_PER_VARIANT", 6)
    result = al.alignments(log, _sequence_net(["A", "B", "C"]))
    assert result["unaligned"]["variants"] >= 1
    variant = next(row for row in result["unaligned_variants"] if row["path"] == ["C", "B", "A", "A", "B"])
    assert variant["cases"] == 1
    assert cases.cases(log, {"variant_id": variant["variant_id"]})["cases"][0]["case_id"] == "c3"
    assert result["cases"] + result["unaligned"]["cases"] == 4


def test_unreachable_final_marking_and_bad_files_are_refused():
    log = _import([["A", "B"]])
    dead = _sequence_net(["A", "B"]).replace(b'<arc id="a1o" source="t1" target="p2"/>', b"")
    with pytest.raises(EventLogWorkspaceError, match="cannot reach its final marking"):
        al.alignments(log, dead)
    with pytest.raises(EventLogWorkspaceError, match="DOCTYPE"):
        al.alignments(log, b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><pnml/>')


def test_fitness_by_case_start_month():
    from io import BytesIO
    from server import event_log_workspace as workspace
    rows = ["case,activity,time"]
    # January: every case fits; February: half skip B.
    for i in range(4):
        for j, a in enumerate(["A", "B", "C"]):
            rows.append(f"j{i},{a},2024-01-{10 + i:02d}T0{j}:00:00Z")
    for i in range(4):
        for j, a in enumerate(["A", "C"] if i % 2 else ["A", "B", "C"]):
            rows.append(f"f{i},{a},2024-02-{10 + i:02d}T0{j}:00:00Z")
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="m", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity",
                                        "timestamp_column": "time"})["log_id"]
    months = al.alignments(log, _sequence_net(["A", "B", "C"]))["by_month"]
    assert months == [{"month": "2024-01", "cases": 4, "fitting_case_share": 1.0, "fitness": 1.0},
                      {"month": "2024-02", "cases": 4, "fitting_case_share": 0.5,
                       "fitness": round(1 - 2 / (2 * 6 + 2 * 5), 4)}]


def test_deviation_causes_find_the_attribute_that_goes_with_deviating_cases(tmp_path):
    from io import BytesIO
    from server import event_log_case_attributes as case_attributes
    from server import event_log_workspace as workspace
    rows, table = ["case,activity,time"], ["id,channel"]
    for i in range(40):
        web = i % 2 == 0
        # Web cases skip B in 3 of 4; branch cases always follow the net.
        steps = ["A", "C"] if web and i % 8 != 0 else ["A", "B", "C"]
        for j, a in enumerate(steps):
            rows.append(f"c{i},{a},2024-01-{1 + i % 28:02d}T0{j}:00:{i % 60:02d}Z")
        table.append(f"c{i},{'web' if web else 'branch'}")
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="dc", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity",
                                        "timestamp_column": "time"})["log_id"]
    (tmp_path / "channels.csv").write_text("\n".join(table) + "\n")
    case_attributes.add(log, tmp_path / "channels.csv", id_column="id", columns=["channel"])
    r = al.deviation_causes(log, _sequence_net(["A", "B", "C"]))
    assert r["cases"] == 40 and r["fitting_cases"] == 25 and r["non_fitting_paths"] == 1
    assert r["outcome_cases"] == 15 and r["outcome"]["kind"] == "variant_in"
    web = next(a for a in r["associations"] if a["feature"]["value"] == "channel=web")
    assert web["outcome_rate_with"] == 0.75 and web["outcome_rate_without"] == 0 and web["holds_after_bonferroni"]
    # A net every case fits (B can be skipped silently) has nothing to explain.
    fits = al.deviation_causes(log, _sequence_net(["A", "B", "C"], silent_skip=1))
    assert fits["non_fitting_paths"] == 0 and fits["associations"] == []


def test_happy_path_net_from_the_most_common_variant():
    log = _import([["A", "B", "C"]] * 6 + [["A", "C"]] * 3 + [["A", "B", "B", "C"]])
    xml, meta = pnml.variant_pnml(log)
    assert meta["path"] == ["A", "B", "C"] and meta["cases"] == 6 and meta["share"] == 0.6
    r = al.alignments(log, xml.encode())
    assert r["fitness"]["fitting_cases"] == 6
    assert {m["activity"]: m["cases"] for m in r["model_moves"]} == {"B": 3}
    assert {m["activity"]: m["cases"] for m in r["log_moves"]} == {"B": 1}
    second, meta2 = pnml.variant_pnml(log, rank=2)
    assert meta2["path"] == ["A", "C"]
    with pytest.raises(EventLogWorkspaceError, match="only 3 distinct paths"):
        pnml.variant_pnml(log, rank=4)


def test_one_case_aligned_on_the_discovered_or_given_net(tmp_path, monkeypatch):
    from server import event_log_agent_sharing as sharing
    log = _import([["A", "B", "C"]] * 8 + [["A", "C"]])
    r = al.case_alignment(log, "c8", noise=0.5)
    assert r["path"] == ["A", "C"] and r["aligned"] and r["skipped_steps"] == ["B"] and not r["fits"]
    given = al.case_alignment(log, "c0", pnml=_sequence_net(["A", "C"]))
    assert given["extra_steps"] == ["B"] and given["model"] == {"uploaded": True}
    with pytest.raises(EventLogWorkspaceError, match="no case"):
        al.case_alignment(log, "nope")
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    sharing.set_share(log, "aggregates")
    with pytest.raises(sharing.NotShared):
        sharing.case_alignment(log, "c8")
    sharing.set_share(log, "cases")
    assert sharing.case_alignment(log, "c8", noise=0.5)["skipped_steps"] == ["B"]


def test_non_fitting_cases_csv_lists_each_deviating_case():
    import csv
    import io
    log = _import([["A", "B", "C"]] * 3 + [["A", "C"]] * 2 + [["A", "B", "  =X", "C"]])
    text, meta = al.non_fitting_cases_csv(log, _sequence_net(["A", "B", "C"]))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert meta == {"cases": 6, "non_fitting_cases": 3, "unaligned_cases": 0, "truncated": False}
    by_id = {r["case_id"]: r for r in rows}
    assert by_id["c3"]["skipped_steps"] == "B" and by_id["c3"]["extra_steps"] == ""
    assert by_id["c5"]["extra_steps"] == "'  =X" and by_id["c5"]["activities"] == "A > B >   =X > C"
    assert by_id["c3"]["alignment_fitness"] == str(round(1 - 1 / 5, 4))


def test_tag_non_fitting_cases_tags_each_deviating_case(monkeypatch):
    from server import event_log_case_tags as case_tags
    log = _import([["A", "B", "C"]] * 3 + [["A", "C"]] * 2 + [["A", "B", "X", "C"]])
    net = _sequence_net(["A", "B", "C"])
    r = al.tag_non_fitting_cases(log, net, "off-model")
    assert (r["tagged"], r["non_fitting_cases"], r["non_fitting_paths"], r["paths_tagged"]) == (3, 3, 2, 2)
    assert r["cases_not_tagged"] == 0 and r["unaligned_cases"] == 0
    listed = case_tags.listing(log, "off-model")
    assert sorted(c["case_id"] for c in listed["cases"]) == ["c3", "c4", "c5"]
    assert listed["cases"][0]["note"] == "does not fit the reference Petri net"
    again = al.tag_non_fitting_cases(log, net, "off-model")
    assert again["already_tagged"] == 3
    # The case filter limits what is tagged: only cases containing X.
    only_x = al.tag_non_fitting_cases(log, net, "extra-step", cohort={"activity": "X"})
    assert only_x["tagged"] == 1 and only_x["non_fitting_paths"] == 1
    fits = _import([["A", "B", "C"]] * 2)
    with pytest.raises(EventLogWorkspaceError, match="nothing to tag"):
        al.tag_non_fitting_cases(fits, net, "x")
    with pytest.raises(EventLogWorkspaceError):
        al.tag_non_fitting_cases(log, net, "")
    monkeypatch.setattr(al, "MAX_TAG_VARIANTS", 1)
    capped = al.tag_non_fitting_cases(log, net, "capped")
    assert capped["paths_tagged"] == 1 and capped["tagged"] == 2 and capped["cases_not_tagged"] == 1


def test_tag_non_fitting_route(tmp_path):
    import os
    import subprocess
    import sys
    net = _sequence_net(["A", "B", "C"]).decode()
    code = r'''
import sys
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
net = sys.argv[1].encode()
doc = "case,activity,time\n" + "".join(f"c{i},A,2024-01-0{i+1}T00:00:00Z\nc{i},B,2024-01-0{i+1}T01:00:00Z\nc{i},C,2024-01-0{i+1}T02:00:00Z\n" for i in range(3)) + "d,A,2024-01-05T00:00:00Z\nd,C,2024-01-05T01:00:00Z\n"
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    url='/v1/event-logs/'+made.json()['log_id']+'/petri-net-alignments/tag?new_tag=off'
    assert client.post(url,content=net,headers={'Content-Type':'application/xml'}).status_code in (401,403)
    r=client.post(url,content=net,headers={**owner,'Content-Type':'application/xml'})
    assert r.status_code==200 and r.json()['tagged']==1, r.text
    assert client.post(url.replace('new_tag=off','new_tag='),content=net,headers={**owner,'Content-Type':'application/xml'}).status_code==422
    assert client.post(url,content=b'<nope/>',headers={**owner,'Content-Type':'application/xml'}).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code, net], env=env, check=True, timeout=60)
