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

from collections import Counter, defaultdict
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-next-activity.v1"
END = None  # the case ends
_SEP = "\x1f"
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


def _parse(spec: Any) -> NextActivitySpec:
    try:
        parsed = NextActivitySpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "next-activity settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


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
                self.counts[h][tuple(path[i + 1 - h:i + 1])][following] += 1

    def predict(self, prefix: list[str], min_support: int) -> tuple[Counter, int]:
        for h in range(min(self.history, len(prefix)), -1, -1):
            seen = self.counts[h].get(tuple(prefix[len(prefix) - h:]) if h else ())
            if seen and sum(seen.values()) >= min_support:
                return seen, h
        return self.counts[0].get((), Counter()), 0


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


def next_activity(log_id: str, spec: Any) -> dict[str, Any]:
    settings = _parse(spec)
    ends = set(settings.end_activities)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        rows = conn.execute(f"""SELECT case_id, string_agg(activity, '{_SEP}' ORDER BY ts, event_id), max(ts)
            FROM ce GROUP BY case_id""").fetchall()
        return {"rows": rows}

    data = workspace._query(log_id, settings.case_type, run)
    completed, open_cases = [], []
    for case_id, path, last in data["rows"]:
        steps = path.split(_SEP)
        (completed if steps[-1] in ends else open_cases).append((case_id, steps, last))
    if not completed:
        raise workspace.EventLogWorkspaceError("no completed cases: none ends with one of the end activities")
    model = _Model(settings.history)
    for _, steps, _ in completed:
        model.learn(steps)
    predictions, expected = [], Counter()
    for case_id, steps, last in sorted(open_cases, key=lambda c: (c[2], c[0]), reverse=True):
        counter, level = model.predict(steps, settings.min_support)
        total = sum(counter.values())
        if not total:
            continue
        for activity, n in counter.items():
            expected[activity] += n / total
        if len(predictions) < settings.limit:
            predictions.append({
                "case_id": case_id, "last_activity": steps[-1], "events": len(steps),
                "last_event_at": last.isoformat() + "Z",
                "next": [{"activity": a, "case_ends": a is END, "probability": round(n / total, 6)}
                         for a, n in _ranked(counter, settings.top)],
                "history_used": level, "support": total})
    return {
        "format": FORMAT,
        "case_notion": data["case_notion"],
        "completed_cases": len(completed),
        "open_cases": len(open_cases),
        "history": settings.history,
        "predictions": predictions,
        "open_cases_shown": len(predictions),
        "expected_next_across_open_cases": [
            {"activity": a, "case_ends": a is END, "expected_cases": round(v, 3)}
            for a, v in sorted(expected.items(), key=lambda kv: (-kv[1], kv[0] is None, kv[0] or ""))[:20]],
        "backtest": _backtest([(steps, last) for _, steps, last in completed], settings),
        "interpretation": ("Estimates of what usually followed the same recent activities in completed cases of this "
                           "log. They are not instructions or commitments, and they say nothing about who should act. "
                           "Check the backtest before relying on them."),
    }
