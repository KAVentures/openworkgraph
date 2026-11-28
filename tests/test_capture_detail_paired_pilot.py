"""Synthetic-only regression tests for the offline paired workbench."""
import copy
import json

import pytest

from evals.reconstruction import capture_detail_pilot as pilot


def event(i, kind="browser_click", **meta):
    return {"event_id": i, "event_type": kind, "observed_at": "2026-01-01T12:00:00Z",
            "metadata": meta}


def case(cid="c1", family="f1", split="holdout", origin="recorded"):
    return {"case_id": cid, "family_id": family, "split": split, "origin": origin,
            "presented_evidence": [event("a"), event("b"), event("c"),
                event("d"), event("e", "browser_control_hover_dwell"),
                event("p", "browser_pointer_trajectory")]}


def label(cid, groups, noise=(), unknown=()):
    return {"case_id": cid, "workflows": [{"event_ids": list(g)} for g in groups],
            "unassigned_event_ids": list(noise), "unknown_event_ids": list(unknown)}


def run(cases=None):
    cases = cases or [case()]
    plan, first, second, review = pilot.make_plan(cases, "holdout", "a" * 64)
    labels, predictions = [], []
    for row in plan["sessions"]:
        labels.append(label(row["case_id"], [("a", "b", "e"), ("c",)], ("d", "p")))
        predictions += [
            label(row["arms"]["standard"]["id"], [("a", "c"), ("b",)], ("d",)),
            label(row["arms"]["detailed"]["id"], [("a", "b", "e"), ("c",)], ("d", "p")),
        ]
    return cases, plan, first, second, review, labels, predictions


def test_identical_structural_denominator_and_false_link_witness():
    cases, plan, _, _, _, labels, predictions = run()
    report = pilot.evaluate(cases, plan, labels, labels, predictions)
    row = report["per_session"][0]
    assert row["standard"]["eligible_events"] == row["detailed_on_standard"]["eligible_events"] == 4
    assert row["detailed_full_diagnostic"]["eligible_events"] == 6
    assert row["standard"]["false_link_pairs"] == 1
    assert row["standard"]["false_link_examples"] == [["a", "c"]]
    assert row["detailed_on_standard"]["false_link_pairs"] == 0
    assert row["standard"]["metrics"]["pair_f1"] == 0
    assert row["detailed_on_standard"]["metrics"]["pair_f1"] == 1
    assert report["recorded_sessions_descriptive"]["pair_f1"]["family_macro_delta"] == 1
    assert not report["automatic_acceptance"]


def test_plan_blinding_and_pinned_exact_input():
    cases, plan, first, second, review, labels, predictions = run()
    assert first[0]["case_id"] != second[0]["case_id"]
    assert {len(first[0]["presented_evidence"]), len(second[0]["presented_evidence"])} == {4, 6}
    assert set(first[0]) == {"case_id", "system_instruction", "presented_evidence"}
    assert review[0]["case_id"] == "c1"
    assert not review[0]["annotation_template"]["workflows"]
    assert pilot.make_plan(cases, "holdout", "a" * 64)[0] == plan
    changed = copy.deepcopy(cases)
    changed[0]["presented_evidence"][0]["metadata"]["action"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        pilot.evaluate(changed, plan, labels, labels, predictions)
    plan["sessions"][0]["arms"]["detailed"]["prompt_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        pilot.evaluate(cases, plan, labels, labels, predictions)


def test_reviewers_group_order_irrelevant_and_disagreement_requires_adjudication():
    cases, plan, _, _, _, labels, predictions = run()
    reordered = [label("c1", [("c",), ("e", "b", "a")], ("p", "d"))]
    assert pilot.evaluate(cases, plan, labels, reordered, predictions)["adjudicated_sessions"] == 0
    other = [label("c1", [("a", "b"), ("c",)], ("d", "p"), ("e",))]
    with pytest.raises(ValueError):
        pilot.evaluate(cases, plan, labels, other, predictions)
    result = pilot.evaluate(cases, plan, labels, other, predictions, adjudications=other)
    assert result["adjudicated_sessions"] == 1
    assert result["per_session"][0]["reviewer_agreement"]["unknown_status_disagreements"] == 1
    with pytest.raises(ValueError):
        pilot.evaluate(cases, plan, labels, labels, predictions, adjudications=other)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicated", "unknown", "omitted", "overlap"])
def test_prediction_exact_population_and_partition(change):
    cases, plan, _, _, _, labels, predictions = run()
    if change == "missing":
        predictions.pop()
    elif change == "extra":
        predictions.append(label("extra", []))
    elif change == "duplicated":
        predictions[1] = predictions[0]
    elif change == "unknown":
        predictions[0]["workflows"][0]["event_ids"].append("e")
    elif change == "omitted":
        predictions[0]["unassigned_event_ids"] = []
    else:
        predictions[0]["unknown_event_ids"].append("a")
    with pytest.raises(ValueError):
        pilot.evaluate(cases, plan, labels, labels, predictions)


def test_unknown_truth_is_withheld_and_predictions_abstaining_do_not_get_credit():
    truth = pilot.partition(label("x", [("a", "b")], ("c",), ("d",)), {"a", "b", "c", "d"})
    pred = pilot.partition(label("x", [("a", "d")], (), ("b", "c")), {"a", "b", "c", "d"})
    out = pilot.measure("x", [event(i) for i in "abcd"], truth, pred)
    assert out["eligible_events"] == 3
    assert out["truth_unknown_events"] == 1
    assert out["prediction_unknown_events"] == 2
    assert out["metrics"]["interruption_rejection"] == 0
    assert out["missed_link_pairs"] == 1
    assert out["false_link_pairs"] == 0


def test_all_unknown_never_earns_perfect_scores():
    truth = pilot.partition(label("x", [], (), ("a", "b")), {"a", "b"})
    pred = pilot.partition(label("x", [("a", "b")]), {"a", "b"})
    out = pilot.measure("x", [event("a"), event("b")], truth, pred)
    assert out["eligible_events"] == 0
    assert all(value is None for value in out["metrics"].values())


def test_no_detail_retained_and_synthetic_separate():
    cases = [case(), case("c2", "f1", origin="synthetic"), case("c3", "f2")]
    cases[2]["presented_evidence"] = cases[2]["presented_evidence"][:4]
    plan, *_ = pilot.make_plan(cases, "holdout", "b" * 64)
    labels, predictions = [], []
    for c, row in zip(cases, plan["sessions"]):
        has_detail = len(c["presented_evidence"]) == 6
        groups = [("a", "b", "e") if has_detail else ("a", "b"), ("c",)]
        noise = ("d", "p") if has_detail else ("d",)
        labels.append(label(c["case_id"], groups, noise))
        predictions.extend([label(row["arms"]["standard"]["id"], [("a", "c"), ("b",)], ("d",)),
                            label(row["arms"]["detailed"]["id"], groups, noise)])
    report = pilot.evaluate(cases, plan, labels, labels, predictions)
    assert report["sessions_without_detail"] == 1
    assert report["declared_recorded_sessions"] == 2
    assert report["declared_synthetic_sessions"] == 1
    assert report["recorded_sessions_descriptive"]["pair_f1"]["paired_families"] == 2


def test_family_macro_not_event_or_session_weighted():
    def row(family, value):
        return {"family_id": family, "standard": {"metrics": dict.fromkeys(pilot.METRICS, 0)},
                "detailed_on_standard": {"metrics": dict.fromkeys(pilot.METRICS, value)}}
    result = pilot.family_summary([row("f1", 1), row("f1", 1), row("f2", 0)])
    assert result["pair_f1"]["family_macro_delta"] == .5
    assert result["pair_f1"]["paired_sessions"] == 3


@pytest.mark.parametrize("mutation", ["family_leak", "duplicate_case", "duplicate_event", "raw_points",
                                      "truth", "identity", "naive_timestamp", "reordered"])
def test_input_validation(mutation):
    cases = [case()]
    e = cases[0]["presented_evidence"]
    if mutation == "family_leak":
        cases.append(case("c2", split="development"))
    elif mutation == "duplicate_case":
        cases.append(case())
    elif mutation == "duplicate_event":
        e.append(copy.deepcopy(e[-1]))
    elif mutation == "raw_points":
        e[0]["metadata"]["nested"] = {"points": [[0, 1, 2]]}
    elif mutation == "truth":
        e[0]["metadata"]["ground_truth"] = {}
    elif mutation == "identity":
        e[0]["actor_id"] = "private"
    elif mutation == "naive_timestamp":
        e[0]["observed_at"] = "2026-01-01T12:00:00"
    else:
        e.reverse()
    with pytest.raises(ValueError):
        pilot.validate_cases(cases)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '[]'])
def test_jsonl_loader_refuses_ambiguous_values(tmp_path, text):
    path = tmp_path / "input.jsonl"
    path.write_text(text)
    with pytest.raises(ValueError):
        pilot.load(path, lines=True)


def test_cli_exclusive_outputs_and_complete_score(tmp_path):
    cases = [case()]
    source = tmp_path / "cases.jsonl"
    pilot.write(source, cases, lines=True)
    target = tmp_path / "private"
    prep = ["prepare", "--cases", str(source), "--split", "holdout",
            "--out-dir", str(target), "--acknowledge-reviewed"]
    assert pilot.main(prep) == 0
    with pytest.raises(SystemExit) as exc:
        pilot.main(prep)
    assert exc.value.code == 2
    plan = json.loads((target / "private-plan.json").read_text())
    row = plan["sessions"][0]["arms"]
    reviewer = [label("c1", [("a", "b", "e"), ("c",)], ("d", "p"))]
    pred = [label(row["standard"]["id"], [("a", "b"), ("c",)], ("d",)),
            label(row["detailed"]["id"], [("a", "b", "e"), ("c",)], ("d", "p"))]
    reviews = tmp_path / "reviews.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    pilot.write(reviews, reviewer, lines=True)
    pilot.write(predictions, pred, lines=True)
    output = tmp_path / "result.json"
    assert pilot.main(["score", "--cases", str(source), "--plan", str(target / "private-plan.json"),
                       "--reviewer-a", str(reviews), "--reviewer-b", str(reviews),
                       "--predictions", str(predictions), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["sessions"] == 1


def test_real_formatter_contract_detail_is_removed_without_reprojecting():
    from shared.evidence import rich_evidence_row
    source = case()
    source["presented_evidence"] = [rich_evidence_row(e) for e in source["presented_evidence"]]
    plan, first, second, _ = pilot.make_plan([source], "holdout", "c" * 64)
    standard, full = sorted([first[0], second[0]], key=lambda p: len(p["presented_evidence"]))
    assert full["presented_evidence"][-1]["interaction_detail"]["context_only"] is True
    assert standard["presented_evidence"] == full["presented_evidence"][:4]
    assert plan["cohort_sha256"] == pilot.digest([source])


def test_undefined_support_is_visible_in_both_arms():
    a, b = dict.fromkeys(pilot.METRICS, .3), dict.fromkeys(pilot.METRICS, .8)
    missing = dict(a, pair_precision=None)
    def row(family, baseline, enhanced):
        return {"family_id": family, "standard": {"metrics": baseline},
                "detailed_on_standard": {"metrics": enhanced}}
    result = pilot.family_summary([row("f1", a, b), row("f1", missing, b)])["pair_precision"]
    assert result["paired_sessions"] == 1
    assert result["sessions_excluded_from_pair"] == 1
    assert result["undefined_standard_sessions"] == 1
    assert result["undefined_detailed_sessions"] == 0
    assert result["paired_family_macro_standard"] == .3
    assert result["paired_family_macro_detailed"] == .8
