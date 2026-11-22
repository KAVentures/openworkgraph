"""Decision mining: which data goes with which branch after a decision activity.

Process-mining tools (ProM's decision miner, PM4Py decision mining, Celonis and Signavio decision
analyses) look at the points where cases take different routes and learn, from the data recorded on
the case, rules such as "amount ≥ 5000 → Approve by manager". This module does the same on a saved
event log, in three steps:

1. **Decision points**: activities after which cases continue to at least two different next
   activities (or end), each seen at least ``MIN_BRANCH_OCCURRENCES`` times.
2. **Features known at the decision**: the case's attributes, plus the latest value of every event
   attribute recorded on that case at or before the decision event. Values recorded later are never
   used, so a rule cannot lean on what happened after the choice. Case attributes carry no time, so
   they may have been set after the decision; the result says so.
3. **A small decision tree** (CART, Gini impurity, depth ≤ ``MAX_DEPTH``) learned on about 70% of the
   cases and scored on the other 30%, split by a hash of the case id so it is repeatable and every
   occurrence of a case lands on one side. Accuracy is reported next to the majority-branch baseline.

Rules describe associations in this log. They are not the business rule, not causes, and they
authorize nothing; a rule that does not beat the baseline on held-out cases is flagged as such.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_decision_mining.v1"
END = "(end)"
MIN_BRANCH_OCCURRENCES = 5
MAX_DEPTH = 4
DEFAULT_DEPTH = 3
MAX_OCCURRENCES = 200_000
MAX_FEATURES = 60
MAX_CATEGORIES = 30
MIN_HOLDOUT = 20
TRAIN_SHARE = 0.7
MAX_DECISION_POINTS = 50
MAX_TEXT = 100


def _check_activity(activity: Any) -> str:
    if not isinstance(activity, str) or not activity or len(activity) > 200:
        raise EventLogWorkspaceError("activity must be a non-empty string of at most 200 characters")
    return activity


def _check_depth(depth: Any) -> int:
    if type(depth) is not int or not 1 <= depth <= MAX_DEPTH:
        raise EventLogWorkspaceError(f"max_depth must be an integer from 1 to {MAX_DEPTH}")
    return depth


_STEPS = """CREATE OR REPLACE TEMP TABLE dm AS SELECT case_id, activity, ts, event_id, attributes,
      coalesce(lead(activity) OVER w, ?) AS next, row_number() OVER w AS pos
    FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id)"""


def _terminal_label(conn) -> str:
    """Choose a display key distinct from every literal activity in the cohort."""
    alternative = "(case ended"
    labels = {row[0] for row in conn.execute(
        "SELECT DISTINCT activity FROM ce WHERE activity = ? OR starts_with(activity, ?)",
        [END, alternative]).fetchall()}
    if END not in labels:
        return END
    label = alternative + ")"
    suffix = 2
    while label in labels:
        label = f"{alternative} {suffix})"
        suffix += 1
    return label


def decision_points(log_id: str, case_type: str | None = None, *,
                    cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    """Activities after which cases go different ways, with the share of each branch."""

    def run(conn):
        terminal_label = _terminal_label(conn)
        conn.execute(_STEPS, [terminal_label])
        rows = conn.execute("""SELECT activity, next, count(*), count(DISTINCT case_id) FROM dm
            GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2""").fetchall()
        cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        return {"rows": rows, "cases": cases, "terminal_label": terminal_label}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    terminal = data["terminal_label"]
    grouped: dict[str, list[tuple[str, int, int]]] = {}
    for activity, nxt, n, cases in data.pop("rows"):
        grouped.setdefault(activity, []).append((nxt, n, cases))
    points = []
    for activity, branches in grouped.items():
        kept = [b for b in branches if b[1] >= MIN_BRANCH_OCCURRENCES]
        if len(kept) < 2:
            continue
        total = sum(b[1] for b in branches)
        points.append({"activity": activity, "occurrences": total,
                       "branches": [{"next": nxt, "next_is_terminal": nxt == terminal,
                                     "occurrences": n, "cases": c, "share": round(n / total, 4)}
                                    for nxt, n, c in branches],
                       "rare_branch_occurrences": total - sum(b[1] for b in kept)})
    points.sort(key=lambda p: (-p["occurrences"], p["activity"]))
    return {"format": FORMAT, **{k: v for k, v in data.items()},
            "min_branch_occurrences": MIN_BRANCH_OCCURRENCES,
            "decision_points": points[:MAX_DECISION_POINTS],
            "decision_points_total": len(points),
            "note": f"'{terminal}' means the case ended after the activity."}


def branch_cases(log_id: str, case_type: str | None = None, *, selection: dict[str, Any],
                 cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    """Page through cases contributing to an observed decision-point branch."""
    if not isinstance(selection, dict) or selection.keys() - {"activity", "next_activity", "ends_here", "after", "limit"}:
        raise EventLogWorkspaceError("unknown decision branch setting")
    activity = _check_activity(selection.get("activity"))
    terminal = "ends_here" in selection
    if terminal and (selection["ends_here"] is not True or "next_activity" in selection):
        raise EventLogWorkspaceError("choose a next activity or a case ending")
    next_activity = None if terminal else _check_activity(selection.get("next_activity"))
    after, limit = selection.get("after"), selection.get("limit", 50)
    if after is not None and (not isinstance(after, str) or not after or len(after) > 200):
        raise EventLogWorkspaceError("invalid case key cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise EventLogWorkspaceError("limit must be a whole number from 1 to 100")

    def run(conn):
        terminal_label = _terminal_label(conn)
        if not terminal and next_activity == terminal_label:
            return {"rows": [], "total": 0, "occurrences": 0}
        conn.execute(_STEPS, [terminal_label])
        conn.execute("""CREATE TEMP TABLE decision_branch_cases AS
            SELECT case_id, count(*) AS occurrences FROM dm
            WHERE activity = ? AND next = ? GROUP BY case_id""",
                     [activity, terminal_label if terminal else next_activity])
        total, occurrences = conn.execute(
            "SELECT count(*), coalesce(sum(occurrences), 0) FROM decision_branch_cases").fetchone()
        rows = conn.execute("""SELECT case_id, occurrences FROM decision_branch_cases
            WHERE ? IS NULL OR case_id > ? ORDER BY case_id LIMIT ?""", [after, after, limit + 1]).fetchall()
        return {"rows": rows, "total": total, "occurrences": occurrences}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data["rows"]
    return {"activity": activity, "next_activity": next_activity, "next_is_terminal": terminal,
            "case_notion": data["case_notion"], "total": data["total"], "occurrences": data["occurrences"],
            "cases": [{"case_id": key, "occurrences": count} for key, count in rows[:limit]],
            "next_after": rows[limit - 1][0] if len(rows) > limit else None}


def _train(case_id: str) -> bool:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < TRAIN_SHARE


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            x = float(value)
        except OverflowError:
            return None
        return x if math.isfinite(x) else None
    if isinstance(value, str):
        try:
            x = float(value.strip())
        except ValueError:
            return None
        return x if math.isfinite(x) else None
    return None


def _text(value: Any) -> str:
    return (value if isinstance(value, str) else json.dumps(value, sort_keys=True))[:MAX_TEXT]


def _features(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each attribute is numeric when ≥ 90% of its present values are numbers, else categorical."""
    seen: dict[str, list[Any]] = {}
    for sample in samples:
        for name, value in sample["x"].items():
            seen.setdefault(name, []).append(value)
    features = []
    for name in sorted(seen, key=lambda n: (-len(seen[n]), n))[:MAX_FEATURES]:
        values = seen[name]
        numbers = [x for x in (_number(v) for v in values) if x is not None]
        if len(numbers) >= 0.9 * len(values) and len(set(numbers)) > 1:
            features.append({"name": name, "kind": "numeric"})
        else:
            counts = Counter(_text(v) for v in values)
            if len(counts) > 1:
                features.append({"name": name, "kind": "categorical",
                                 "values": [v for v, _ in counts.most_common(MAX_CATEGORIES)]})
    return features


def _gini(counts: Counter) -> float:
    total = sum(counts.values())
    return 1.0 - sum((c / total) ** 2 for c in counts.values()) if total else 0.0


def _test(sample: dict[str, Any], split: dict[str, Any]) -> bool:
    value = sample["x"].get(split["feature"])
    if value is None:
        return False
    if split["op"] == "==":
        return _text(value) == split["value"]
    x = _number(value)
    return x is not None and x >= split["value"]


def _numeric_best(rows, name, counts, parent, min_leaf):
    """Best ``x >= t`` split for one numeric attribute, over every boundary between distinct values.

    Rows without a number go to the "no" side, like any row for which the condition does not hold.
    """
    present = sorted(((x, r["y"]) for r in rows if (x := _number(r["x"].get(name))) is not None),
                     key=lambda p: p[0])
    if len(present) < 2:
        return None, 0.0
    total = len(rows)
    yes = Counter(y for _, y in present)
    no = counts - yes
    best, best_gain = None, 0.0
    for i in range(1, len(present)):
        x_prev, y_prev = present[i - 1]
        yes[y_prev] -= 1
        no[y_prev] += 1
        x = present[i][0]
        if x == x_prev:
            continue
        n_yes = len(present) - i
        n_no = total - n_yes
        if n_yes < min_leaf or n_no < min_leaf:
            continue
        gain = parent - (n_yes * _gini(+yes) + n_no * _gini(+no)) / total
        if gain > best_gain + 1e-12:
            # The emitted threshold must reproduce the partition that won.
            # Halving first avoids overflow. If rounding lands at the lower
            # endpoint, the upper observed value is an exact boundary.
            midpoint = x_prev / 2 + x / 2
            threshold = midpoint if x_prev < midpoint <= x else x
            best, best_gain = {"feature": name, "op": ">=", "value": threshold}, gain
    return best, best_gain


def _best_split(rows, features, counts, min_leaf):
    parent = _gini(counts)
    best, best_gain = None, 1e-9
    for feature in features:
        if feature["kind"] == "numeric":
            split, gain = _numeric_best(rows, feature["name"], counts, parent, min_leaf)
            if split is not None and gain > best_gain + 1e-12:
                best, best_gain = split, gain
            continue
        for value in feature["values"]:
            split = {"feature": feature["name"], "op": "==", "value": value}
            yes = Counter(r["y"] for r in rows if _test(r, split))
            n_yes = sum(yes.values())
            n_no = len(rows) - n_yes
            if n_yes < min_leaf or n_no < min_leaf:
                continue
            gain = parent - (n_yes * _gini(yes) + n_no * _gini(counts - yes)) / len(rows)
            if gain > best_gain + 1e-12:
                best, best_gain = split, gain
    return best, best_gain


def _grow(rows, features, depth, max_depth, min_leaf):
    counts = Counter(r["y"] for r in rows)
    node: dict[str, Any] = {"n": len(rows), "counts": counts, "predict": min(counts, key=lambda k: (-counts[k], k))}
    if depth >= max_depth or len(counts) < 2 or len(rows) < 2 * min_leaf:
        return node
    best, best_gain = _best_split(rows, features, counts, min_leaf)
    if best is None:
        return node
    node["split"] = best
    node["gain"] = best_gain
    node["yes"] = _grow([r for r in rows if _test(r, best)], features, depth + 1, max_depth, min_leaf)
    node["no"] = _grow([r for r in rows if not _test(r, best)], features, depth + 1, max_depth, min_leaf)
    return node


def _predict(node, sample) -> str:
    while "split" in node:
        node = node["yes"] if _test(sample, node["split"]) else node["no"]
    return node["predict"]


def _condition(split: dict[str, Any], holds: bool) -> dict[str, Any]:
    categorical = split["op"] == "=="
    op = ("==" if holds else "!=") if categorical else (">=" if holds else "<")
    condition = {"attribute": split["feature"], "op": op, "value": split["value"]}
    if not holds:
        condition["includes_missing"] = True
        if not categorical:
            condition["includes_non_numeric"] = True
    return condition


def _rules(node, path=()) -> list[dict[str, Any]]:
    if "split" not in node:
        n, counts = node["n"], node["counts"]
        return [{"conditions": list(path), "next": node["predict"], "training_occurrences": n,
                 "confidence": round(counts[node["predict"]] / n, 4) if n else 0.0,
                 "branches": {k: v for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}}]
    split = node["split"]
    return (_rules(node["yes"], path + (_condition(split, True),))
            + _rules(node["no"], path + (_condition(split, False),)))


def _importance(node, total: int, into: Counter) -> Counter:
    if "split" in node:
        into[node["split"]["feature"]] += node["gain"] * node["n"] / total
        _importance(node["yes"], total, into)
        _importance(node["no"], total, into)
    return into


def analyse(log_id: str, activity: str, case_type: str | None = None, *, max_depth: int = DEFAULT_DEPTH,
            cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    """Learn which recorded data goes with which branch after ``activity``."""
    activity = _check_activity(activity)
    max_depth = _check_depth(max_depth)

    def run(conn):
        terminal_label = _terminal_label(conn)
        conn.execute(_STEPS, [terminal_label])
        total = conn.execute("SELECT count(*) FROM dm WHERE activity = ?", [activity]).fetchone()[0]
        # Latest value of each event attribute at or before the decision event, plus the case's attributes.
        rows = conn.execute("""
            WITH d AS (SELECT case_id, pos, next FROM dm WHERE activity = $a ORDER BY hash(case_id), pos
                       LIMIT $cap),
            ev AS (SELECT d.case_id, d.pos, kv.key AS k, kv.value AS v, e.pos AS epos
                   FROM d JOIN dm e ON e.case_id = d.case_id AND e.pos <= d.pos,
                     unnest(map_entries(json_transform(coalesce(e.attributes, '{}'),
                            '"MAP(VARCHAR, JSON)"'))) AS m(kv)
                   -- An empty cell records nothing; it must not hide an earlier value.
                   WHERE kv.value IS NOT NULL AND kv.value::VARCHAR NOT IN ('""', 'null')),
            latest AS (SELECT case_id, pos, json_group_object(k, v) AS attrs FROM (
                   SELECT case_id, pos, k, arg_max(v, epos) AS v FROM ev GROUP BY 1, 2, 3) GROUP BY 1, 2)
            SELECT d.case_id, d.pos, d.next, latest.attrs, o.attributes FROM d
              LEFT JOIN latest USING (case_id, pos)
              LEFT JOIN objects o ON o.log_id = $log AND o.object_id = d.case_id""",
                            {"a": activity, "cap": MAX_OCCURRENCES, "log": log_id}).fetchall()
        return {"rows": rows, "total": total, "terminal_label": terminal_label}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    raw, total = data.pop("rows"), data.pop("total")
    if not total:
        raise EventLogWorkspaceError("this activity does not occur in the selected cases")
    samples = []
    for case_id, _pos, nxt, event_attrs, case_attrs in raw:
        x: dict[str, Any] = {}
        for prefix, blob in (("case.", case_attrs), ("event.", event_attrs)):
            try:
                parsed = json.loads(blob) if blob else {}
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if value is not None and value != "":
                        x[prefix + str(key)[:MAX_TEXT]] = value
        samples.append({"case_id": case_id, "y": nxt, "x": x, "train": _train(case_id)})

    terminal = data["terminal_label"]
    branch_counts = Counter(s["y"] for s in samples)
    base = {"format": FORMAT, **data, "activity": activity, "occurrences": total,
            "occurrences_used": len(samples), "occurrences_capped": total > len(samples),
            "branches": [{"next": k, "next_is_terminal": k == terminal,
                          "occurrences": v, "share": round(v / len(samples), 4)}
                         for k, v in sorted(branch_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
            "method": ("CART decision tree (Gini impurity) on ~70% of cases, scored on the other ~30% "
                       "(split by a hash of the case id)"),
            "features_known_at_decision": ("case attributes (case.*) and the latest value of each event attribute "
                                           "recorded at or before the decision event (event.*)"),
            "caveat": ("Rules describe associations in this log, not the business rule or its cause, and "
                       "authorize nothing. Case attributes carry no time and may have been set after the decision.")}
    if len(branch_counts) < 2:
        return {**base, "rules": [], "note": "every occurrence continues the same way, so there is no decision here"}
    train = [s for s in samples if s["train"]]
    test = [s for s in samples if not s["train"]]
    features = _features(train)
    if not features or len(train) < 2 * MIN_BRANCH_OCCURRENCES:
        return {**base, "rules": [], "features": [f["name"] for f in features],
                "note": "not enough cases or recorded data varies too little to learn a rule"}
    min_leaf = max(MIN_BRANCH_OCCURRENCES, round(0.02 * len(train)))
    tree = _grow(train, features, 0, max_depth, min_leaf)
    majority = min(Counter(s["y"] for s in train).items(), key=lambda kv: (-kv[1], kv[0]))[0]
    evaluation: dict[str, Any] = {"training_occurrences": len(train), "holdout_occurrences": len(test)}
    if len(test) >= MIN_HOLDOUT:
        hits = sum(_predict(tree, s) == s["y"] for s in test)
        base_hits = sum(s["y"] == majority for s in test)
        evaluation.update({"holdout_accuracy": round(hits / len(test), 4),
                           "majority_baseline": majority,
                           "majority_baseline_accuracy": round(base_hits / len(test), 4),
                           "beats_baseline": hits > base_hits})
        confusion: dict[str, Counter] = {}
        for s in test:
            confusion.setdefault(s["y"], Counter())[_predict(tree, s)] += 1
        evaluation["holdout_confusion"] = {actual: dict(sorted(c.items())) for actual, c in sorted(confusion.items())}
    else:
        evaluation["note"] = f"fewer than {MIN_HOLDOUT} held-out occurrences: accuracy not assessed"
    importance = _importance(tree, len(train), Counter())
    scale = sum(importance.values()) or 1.0
    rules = sorted(_rules(tree), key=lambda r: (-r["training_occurrences"], r["next"]))
    for rule in rules:
        rule["next_is_terminal"] = rule["next"] == terminal
    return {**base, "max_depth": max_depth, "min_leaf_occurrences": min_leaf,
            "features": [{"name": f["name"], "kind": f["kind"]} for f in features],
            "feature_importance": [{"attribute": k, "share": round(v / scale, 4)}
                                   for k, v in sorted(importance.items(), key=lambda kv: (-kv[1], kv[0]))],
            "rules": rules, "evaluation": evaluation,
            "learned_anything": "split" in tree}


__all__ = ["decision_points", "branch_cases", "analyse", "FORMAT"]
