"""Next-activity prediction for open cases.

From completed cases (whose latest activity is one of the activities that end a
case), learn how often each activity, or the end of the case, followed a given
recent history: the last ``history`` activities. An open case gets the
distribution that followed its own recent history, backing off to a shorter
history when too few completed cases were in that state, and finally to all
transitions. The level used is reported with each prediction.

How good this is gets measured, not assumed. A temporal backtest learns from
cases that ended before a cutoff and predicts every step of the cases that
ended after it, reporting top-1 and top-3 accuracy against two baselines: the
single most frequent next activity overall, and the most frequent successor of
the current activity alone.

Predictions describe this log's history. They are not instructions, targets or
assessments of anyone, and resources are not used.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-next-activity.v1"
END = None  # the case ends
Label = Annotated[str, Field(min_length=1, max_length=200)]


class NextActivitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    end_activities: list[Label] = Field(min_length=1, max_length=20)
    history: StrictInt = Field(default=2, ge=1, le=4)
    min_support: StrictInt = Field(default=5, ge=1, le=10_000)
    top: StrictInt = Field(default=3, ge=1, le=10)
    limit: StrictInt = Field(default=50, ge=1, le=500)
    backtest_share: StrictFloat | StrictInt = Field(default=0.3, gt=0, lt=1)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


class TrainingEvidenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: NextActivitySpec
    case_id: Label
    expected_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    after: tuple[Label, Annotated[StrictInt, Field(ge=1)]] | None = None
    limit: StrictInt = Field(default=50, ge=1, le=100)


def _parse(settings_type, spec: Any):
    try:
        return settings_type.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "next-activity settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None


class _Model:
    """Counts of what followed each recent history of length 1..H, plus all transitions."""

    def __init__(self, history: int):
        self.history = history
        self.counts: list[dict[tuple[str, ...], Counter]] = [defaultdict(Counter) for _ in range(history + 1)]

    def learn(self, path: list[str]) -> None:
        for i in range(len(path)):
            following = path[i + 1] if i + 1 < len(path) else END
            for h in range(self.history + 1):
                if h > i + 1:
                    break
                self.counts[h][_history_at(path, i, h)][following] += 1

    def predict(self, prefix: list[str], min_support: int) -> tuple[Counter, int]:
        for h in range(min(self.history, len(prefix)), -1, -1):
            seen = self.counts[h].get(_history_at(prefix, len(prefix) - 1, h))
            if seen and sum(seen.values()) >= min_support:
                return seen, h
        return self.counts[0].get((), Counter()), 0


def _history_at(path: list[str], index: int, length: int) -> tuple[str, ...]:
    return tuple(path[index + 1 - length:index + 1])


def _ranked(counter: Counter, top: int) -> list[tuple[str | None, int]]:
    # Ties break on the activity name so results are stable; the case ending sorts last among ties.
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0] is None, kv[0] or ""))[:top]


def _backtest(paths: list[tuple[list[str], Any]], settings: NextActivitySpec) -> dict[str, Any]:
    ordered = sorted(paths, key=lambda p: p[1])
    split = int(len(ordered) * (1 - settings.backtest_share))
    train, test = ordered[:split], ordered[split:]
    if len(train) < 10 or len(test) < 5:
        return {"available": False, "reason": "needs at least 10 completed cases before the cutoff and 5 after",
                "train_cases": len(train), "test_cases": len(test)}
    model = _Model(settings.history)
    for path, _ in train:
        model.learn(path)
    overall = _ranked(model.counts[0][()], 1)[0][0]
    steps = hit1 = hit3 = base_overall = base_last = 0
    for path, _ in test:
        for i in range(len(path)):
            actual = path[i + 1] if i + 1 < len(path) else END
            counter, _level = model.predict(path[:i + 1], settings.min_support)
            ranked = [a for a, _ in _ranked(counter, 3)]
            steps += 1
            hit1 += bool(ranked) and ranked[0] == actual
            hit3 += actual in ranked
            base_overall += overall == actual
            last = model.counts[1].get((path[i],))
            base_last += bool(last) and _ranked(last, 1)[0][0] == actual
    return {"available": True, "cutoff_case_ended_at": ordered[split][1].isoformat() + "Z",
            "train_cases": len(train), "test_cases": len(test), "steps_scored": steps,
            "top1_accuracy": round(hit1 / steps, 6), "top3_accuracy": round(hit3 / steps, 6),
            "baseline_most_frequent_next": round(base_overall / steps, 6),
            "baseline_current_activity_only": round(base_last / steps, 6),
            "scoring_note": ("Every step of cases that ended after the cutoff is predicted from a model learned only "
                             "from cases that ended before it; the case ending counts as a possible next step.")}


def _project(log_id: str, settings: NextActivitySpec) -> dict[str, Any]:
    chosen = cohorts.parse_cohort(settings.cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id), max(ts)
            FROM ce GROUP BY case_id""").fetchall()
        return {"rows": rows, "cohort": cohorts.describe(chosen)}

    return workspace._query(log_id, settings.case_type, run)


def _snapshot(data: dict[str, Any], settings: NextActivitySpec) -> str:
    """Fingerprint the selected histories used by the estimate and evidence request."""
    digest = sha256()
    basis = [data["log"]["log_id"], data["case_notion"], sorted(set(settings.end_activities)),
             settings.history, settings.min_support, data["cohort"]]
    digest.update(json.dumps(basis, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode() + b'\n')
    for key, steps, last in sorted(data["rows"], key=lambda row: row[0]):
        digest.update(json.dumps([key, steps, last.isoformat()], ensure_ascii=True, separators=(',', ':')).encode() + b'\n')
    return digest.hexdigest()


def next_activity(log_id: str, spec: Any, *, case_id: str | None = None,
                  include_evidence: bool = False) -> dict[str, Any]:
    settings = _parse(NextActivitySpec, spec)
    data = _project(log_id, settings)
    ends = set(settings.end_activities)
    completed, open_cases = [], []
    case_selection = "not_in_selection"
    for key, steps, last in data["rows"]:
        is_complete = steps[-1] in ends
        (completed if is_complete else open_cases).append((key, steps, last))
        if key == case_id:
            case_selection = "completed" if is_complete else "unsupported"
    if not completed and case_id is None:
        raise workspace.EventLogWorkspaceError("no completed cases: none ends with one of the end activities")
    model = _Model(settings.history)
    for _, steps, _ in completed:
        model.learn(steps)
    predictions, expected = [], Counter()
    for key, steps, last in sorted(open_cases, key=lambda c: (c[2], c[0]), reverse=True):
        counter, level = model.predict(steps, settings.min_support)
        total = sum(counter.values())
        if not total:
            continue
        for activity, n in counter.items():
            expected[activity] += n / total
        if case_id is not None and key == case_id:
            case_selection = "predicted"
        if (case_id is None or key == case_id) and len(predictions) < settings.limit:
            predictions.append({
                "case_id": key, "last_activity": steps[-1], "events": len(steps),
                "last_event_at": last.isoformat() + "Z",
                "next": [{"activity": a, "case_ends": a is END, "probability": round(n / total, 6)}
                         for a, n in _ranked(counter, settings.top)],
                "history_used": level, "support": total,
                **({"matched_history": list(_history_at(steps, len(steps) - 1, level))} if include_evidence else {})})
    return {
        "format": FORMAT,
        "case_notion": data["case_notion"],
        "completed_cases": len(completed),
        "open_cases": len(open_cases),
        "history": settings.history,
        "predictions": predictions,
        **({"evidence_snapshot": _snapshot(data, settings)} if include_evidence else {}),
        **({"case_selection": case_selection} if case_id is not None else {}),
        "open_cases_shown": len(predictions),
        "expected_next_across_open_cases": [
            {"activity": a, "case_ends": a is END, "expected_cases": round(v, 3)}
            for a, v in sorted(expected.items(), key=lambda kv: (-kv[1], kv[0] is None, kv[0] or ""))[:20]],
        "backtest": _backtest([(steps, last) for _, steps, last in completed], settings),
        "interpretation": ("Estimates of what usually followed the same recent activities in completed cases of this "
                           "log. They are not instructions or commitments, and they say nothing about who should act. "
                           "Check the backtest before relying on them."),
    }


def training_evidence(log_id: str, spec: Any) -> dict[str, Any]:
    request = _parse(TrainingEvidenceSpec, spec)
    settings = request.settings
    data = _project(log_id, settings)
    if _snapshot(data, settings) != request.expected_snapshot:
        raise workspace.EventLogWorkspaceError("the prediction history changed; predict next activities again")
    ends = set(settings.end_activities)
    model = _Model(settings.history)
    completed = []
    target = None
    for case_id, path, _ in data["rows"]:
        if path[-1] in ends:
            model.learn(path)
            completed.append((case_id, path))
        elif case_id == request.case_id:
            target = path
    if target is None:
        raise workspace.EventLogWorkspaceError("choose a predicted open case in the current selection")
    counts, length = model.predict(target, settings.min_support)
    if not counts:
        raise workspace.EventLogWorkspaceError("the open case has no matching completed-case steps")
    history = _history_at(target, len(target) - 1, length)
    rows = []
    for case_id, path in sorted(completed, key=lambda row: row[0]):
        if request.after is not None and case_id < request.after[0]:
            continue
        start = max(0, length - 1)
        if request.after is not None and case_id == request.after[0]:
            start = max(start, request.after[1])
        for index in range(start, len(path)):
            if _history_at(path, index, length) != history:
                continue
            next_activity = path[index + 1] if index + 1 < len(path) else END
            rows.append({"case_id": case_id, "step": index + 1, "activity": path[index],
                         "next_activity": next_activity, "case_ends": next_activity is END})
            if len(rows) > request.limit:
                break
        if len(rows) > request.limit:
            break
    page = rows[:request.limit]
    return {"case_id": request.case_id, "matched_history": list(history), "support": sum(counts.values()),
            "occurrences": page, "next_after": [page[-1]["case_id"], page[-1]["step"]] if len(rows) > request.limit else None}
