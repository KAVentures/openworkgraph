"""Ablation of opt-in high-fidelity interaction detail (#634) on reconstruction.

Enhanced and Full fidelity capture add hover dwells, drag and drop and sampled
pointer trajectories. #634 requires that these signals never merge work on
their own and that their effect on reconstruction is measured, not assumed.

This harness takes the committed corpus and adds the interaction detail a
browser would record around each observed browser action:

* a hover dwell on the control just before the action, on the same page, tab
  and record the action was on (the record reference comes from the page, as
  the enrichment does in the extension), and therefore part of the same
  workflow as that action;
* for drags, a drag start and drop around the action, likewise in context;
* sampled pointer trajectories between actions, which carry no target, record
  reference or label and are therefore noise for workflow membership.

It then scores each OWG grouping with and without the added detail, and with
the detail removed again by the ``interaction_detail`` ablation, which must
reproduce the baseline exactly. A grouping that merged work because of a
hover, or that pulled pointer samples into a workflow, shows up here as
contamination or lower precision.

The added events are synthetic and positioned by construction, so these are
engineering checks of how the shipped groupings treat such evidence. They are
not evidence that interaction detail improves reconstruction of real work; that
needs the independently labeled real sessions #634 asks for. Use the
development split while iterating; the holdout stays sealed.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from server.capture_depth import HIGH_FIDELITY_EVENT_TYPES

from .fixtures import _trace_projection, load_cases

FORMAT = "openworkgraph.reconstruction-interaction-detail-ablation.v1"
METRICS = ("workflow_assignment_precision", "workflow_assignment_recall", "workflow_assignment_f1",
           "cross_workflow_contamination", "interruption_rejection")
GROUPINGS = ("record_lanes", "linked_items")
_TRAJECTORY_EVERY = 3
_STRIPPED_META = ("semantic_action", "semantic_action_confidence", "clipboard_transfer_id",
                  "clipboard_source_observed", "work_text", "text")


def _at(value: str, seconds: float) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _detail_event(anchor: dict[str, Any], suffix: str, event_type: str, seconds: float,
                  meta: dict[str, Any], *, keep_context: bool) -> dict[str, Any]:
    out = copy.deepcopy(anchor)
    out["event_id"] = f"{anchor['event_id']}-{suffix}"
    out["event_type"] = event_type
    out["observed_at"] = _at(anchor["observed_at"], seconds)
    out["duration_seconds"] = 0
    old = anchor.get("metadata") if isinstance(anchor.get("metadata"), dict) else {}
    new = {k: v for k, v in old.items() if k not in _STRIPPED_META}
    new.pop("target", None)
    if not keep_context:
        # A pointer sample has no control, record or label: only where it happened.
        new.pop("resource_reference", None)
        new = {k: v for k, v in new.items() if k in {"page", "tab_context_id", "source"}}
        if isinstance(new.get("page"), dict):
            new["page"] = {k: v for k, v in new["page"].items() if k in {"hostname", "pathname"}}
        out["window_title"] = out.get("app") or "Browser"
    new.update(meta)
    out["metadata"] = new
    return out


def _membership(case: dict[str, Any]) -> dict[str, str]:
    return {event_id: wf["workflow_id"] for wf in case["ground_truth"]["workflows"] for event_id in wf["event_ids"]}


def with_interaction_detail(case: dict[str, Any]) -> dict[str, Any]:
    """Return the case with in-context hover/drag events and pointer samples added."""
    membership = _membership(case)
    sources = [e for e in case["source_events"] if isinstance(e, dict)]
    added: list[tuple[dict[str, Any], str | None]] = []
    browser = [e for e in sources if e.get("source") == "browser_extension" and e.get("event_type") not in HIGH_FIDELITY_EVENT_TYPES]
    for index, anchor in enumerate(browser):
        target = (anchor.get("metadata") or {}).get("target") or {}
        role = target.get("role") if isinstance(target.get("role"), str) else ""
        owner = membership.get(anchor["event_id"])
        hover = _detail_event(anchor, "hover", "browser_control_hover_dwell", -0.9, {
            "action": "control_hover_dwell", "target": {"tag": "button", "role": role},
            "duration_ms": 900, "ended_by": "activated"}, keep_context=True)
        added.append((hover, owner))
        if (anchor.get("metadata") or {}).get("action") in {"drag", "drop"} or "drag" in str(anchor.get("event_type")):
            for suffix, kind, offset in (("dragstart", "browser_drag_start", -0.5), ("drop", "browser_drag_drop", 0.2)):
                added.append((_detail_event(anchor, suffix, kind, offset, {
                    "action": kind.removeprefix("browser_"), "target": {"tag": "div", "role": role},
                    "payload_kinds": ["text"]}, keep_context=True), owner))
        if index % _TRAJECTORY_EVERY == 0:
            added.append((_detail_event(anchor, "pointer", "browser_pointer_trajectory", 0.4, {
                "action": "pointer_trajectory", "sample_hz": 4, "coordinates": "viewport_fraction",
                "points": [[0, 0.41, 0.37], [250, 0.44, 0.39], [500, 0.47, 0.42]]}, keep_context=False), None))
    events = sources + [event for event, _ in added]
    events.sort(key=lambda e: (e["observed_at"], e["event_id"]))
    truth = copy.deepcopy(case["ground_truth"])
    by_workflow = {wf["workflow_id"]: wf for wf in truth["workflows"]}
    order = {e["event_id"]: i for i, e in enumerate(events)}
    for event, owner in added:
        if owner in by_workflow:
            by_workflow[owner]["event_ids"].append(event["event_id"])
            # A hover or drag is a UI mechanic of the step it precedes, never a checkpoint.
            by_workflow[owner].setdefault("ui_mechanic_event_ids", []).append(event["event_id"])
        else:
            truth["noise_event_ids"].append(event["event_id"])
    for wf in truth["workflows"]:
        for key in ("event_ids", "ui_mechanic_event_ids"):
            if key in wf:
                wf[key] = sorted(wf[key], key=order.__getitem__)
    truth["noise_event_ids"] = sorted(truth["noise_event_ids"], key=order.__getitem__)
    return {**case, "presented_evidence": [_trace_projection(e) for e in events],
            "source_events": events, "ground_truth": truth}


def without_interaction_detail(case: dict[str, Any]) -> dict[str, Any]:
    """The ``interaction_detail`` ablation: drop every high-fidelity event and its truth."""
    dropped = {e["event_id"] for e in case["source_events"] if e.get("event_type") in HIGH_FIDELITY_EVENT_TYPES}
    truth = copy.deepcopy(case["ground_truth"])
    for wf in truth["workflows"]:
        for key in ("event_ids", "ui_mechanic_event_ids", "automation_relevant_event_ids"):
            if key in wf:
                wf[key] = [i for i in wf[key] if i not in dropped]
        wf["checkpoint_groups"] = [{**g, "event_ids": [i for i in g["event_ids"] if i not in dropped]}
                                   for g in wf.get("checkpoint_groups", [])]
    truth["noise_event_ids"] = [i for i in truth["noise_event_ids"] if i not in dropped]
    return {**case, "presented_evidence": [e for e in case["presented_evidence"] if e.get("event_id") not in dropped],
            "source_events": [e for e in case["source_events"] if e["event_id"] not in dropped],
            "ground_truth": truth}


def _score(grouping: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    from . import owg_native
    report = (owg_native.score_record_lanes(cases) if grouping == "record_lanes"
              else owg_native.score_linked_items(cases))
    return {name: report.get(name) for name in METRICS}


def _predictions(grouping: str, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import owg_native
    predict = owg_native.predict_record_lanes if grouping == "record_lanes" else owg_native.predict_linked_items
    return [predict(case) for case in cases]


def _merges_by_detail(grouping: str, detailed: list[dict[str, Any]]) -> int:
    """Predicted workflows that join two different true workflows only through added detail events."""
    count = 0
    for case, prediction in zip(detailed, _predictions(grouping, detailed)):
        membership = _membership(case)
        detail = {e["event_id"] for e in case["source_events"] if e.get("event_type") in HIGH_FIDELITY_EVENT_TYPES}
        for wf in prediction["workflows"]:
            observed = {membership[i] for i in wf["event_ids"] if i in membership and i not in detail}
            with_detail = {membership[i] for i in wf["event_ids"] if i in membership}
            count += len(with_detail) > max(len(observed), 1)
    return count


def compare(cases: list[dict[str, Any]], groupings: tuple[str, ...] = GROUPINGS) -> dict[str, Any]:
    detailed = [with_interaction_detail(case) for case in cases]
    restored = [without_interaction_detail(case) for case in detailed]
    added = sum(len(d["source_events"]) - len(c["source_events"]) for c, d in zip(cases, detailed))
    rows = {}
    for grouping in groupings:
        baseline, enriched, ablated = _score(grouping, cases), _score(grouping, detailed), _score(grouping, restored)
        pointer_ids = {e["event_id"] for d in detailed for e in d["source_events"]
                       if e.get("event_type") == "browser_pointer_trajectory"}
        pointer_assigned = sum(1 for p in _predictions(grouping, detailed) for wf in p["workflows"]
                               for i in wf["event_ids"] if i in pointer_ids)
        rows[grouping] = {
            "baseline": baseline, "with_interaction_detail": enriched, "detail_removed": ablated,
            "delta": {name: (None if baseline[name] is None or enriched[name] is None
                             else round(enriched[name] - baseline[name], 6)) for name in METRICS},
            "ablation_reproduces_baseline": ablated == baseline,
            "workflows_joined_only_by_interaction_detail": _merges_by_detail(grouping, detailed),
            "pointer_samples_assigned_to_a_workflow": pointer_assigned,
        }
    return {"format": FORMAT, "cases": len(cases), "interaction_detail_events_added": added,
            "groupings": rows,
            "interpretation": ("Synthetic interaction detail positioned by construction around committed fixture "
                               "actions. Checks that the shipped groupings do not merge work or assign pointer "
                               "samples because of hover, drag or pointer events. Not evidence of improvement on "
                               "real work; that requires independently labeled real sessions.")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score reconstruction with and without interaction detail (#634)")
    parser.add_argument("--cases", help="optional exported JSONL corpus")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="development")
    parser.add_argument("--grouping", choices=GROUPINGS, action="append")
    args = parser.parse_args(argv)
    cases = load_cases(Path(args.cases)) if args.cases else load_cases()
    if args.split != "all":
        from .prepare import split_cases
        cases = split_cases(cases, args.split)
    print(json.dumps(compare(cases, tuple(args.grouping or GROUPINGS)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
