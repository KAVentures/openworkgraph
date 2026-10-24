"""Saved signal rules over event logs (roadmap #326 F2).

A rule is a typed, immutable condition on one saved log: a KPI threshold
("median throughput above 3 days") or a case condition ("cases open longer than
48 hours without Close"). Evaluating rules returns which ones are triggered,
the measured value and the evidence cases, plus which cases are new since the
previous evaluation.

Signals are descriptive and local. They never send messages, change source
systems or run automations; acting on one is the user's decision. Rules are
stored with the log and deleted with it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_compliance as compliance
from . import event_log_workspace as workspace

MAX_RULES = 50
MAX_EVIDENCE_CASES = 50
MAX_REMEMBERED_CASES = 10_000
Label = Annotated[str, Field(min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class KpiCondition(_Strict):
    kind: Literal["kpi"]
    metric: Literal["case_count", "median_throughput_seconds", "p90_throughput_seconds",
                    "rework_rate", "sla_breach_rate"]
    operator: Literal["above", "below"]
    threshold: Union[StrictInt, StrictFloat] = Field(ge=0)
    sla_seconds: StrictInt | None = Field(default=None, ge=1, le=10 ** 9)


class CaseCondition(_Strict):
    kind: Literal["cases"]
    condition: Literal["duration_over", "contains_activity", "lacks_activity", "has_rework", "open_longer_than"]
    seconds: StrictInt | None = Field(default=None, ge=1, le=10 ** 9)
    activity: Label | None = None
    end_activities: list[Label] = Field(default_factory=list, max_length=20)
    minimum_cases: StrictInt = Field(default=1, ge=1, le=10 ** 9)


class ComplianceCondition(_Strict):
    """A declarative compliance rule (see event_log_compliance); triggers on violating cases."""
    kind: Literal["compliance"]
    rule: compliance.Rule
    minimum_cases: StrictInt = Field(default=1, ge=1, le=10 ** 9)


class SignalRule(_Strict):
    name: str = Field(min_length=1, max_length=120)
    case_type: Label | None = None
    cohort: dict[str, Any] = Field(default_factory=dict)
    when: Annotated[Union[KpiCondition, CaseCondition, ComplianceCondition], Field(discriminator="kind")]


def _validate(spec: Any) -> SignalRule:
    try:
        rule = SignalRule.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "signal rule is invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(rule.cohort)
    when = rule.when
    if isinstance(when, KpiCondition):
        if when.metric == "sla_breach_rate" and not when.sla_seconds:
            raise workspace.EventLogWorkspaceError("sla_breach_rate needs sla_seconds")
        if when.metric.endswith("_rate") and when.threshold > 1:
            raise workspace.EventLogWorkspaceError("rate thresholds are fractions between 0 and 1")
    elif isinstance(when, ComplianceCondition):
        compliance.parse({"rules": [when.rule.model_dump()]})
    else:
        needs_seconds = when.condition in {"duration_over", "open_longer_than"}
        needs_activity = when.condition in {"contains_activity", "lacks_activity"}
        if needs_seconds != (when.seconds is not None):
            raise workspace.EventLogWorkspaceError(f"{when.condition} {'needs' if needs_seconds else 'does not use'} seconds")
        if needs_activity != (when.activity is not None):
            raise workspace.EventLogWorkspaceError(f"{when.condition} {'needs' if needs_activity else 'does not use'} an activity")
        if (when.condition == "open_longer_than") != bool(when.end_activities):
            raise workspace.EventLogWorkspaceError("open_longer_than needs the activities that end a case, and only it uses them")
    return rule


def _public(rule_id: str, created_at: datetime, rule: SignalRule, evaluated_at: datetime | None) -> dict[str, Any]:
    return {"rule_id": rule_id, "created_at": created_at.isoformat() + "Z", **rule.model_dump(),
            "last_evaluated_at": evaluated_at.isoformat() + "Z" if evaluated_at else None}


def list_rules(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT rule_id, created_at, definition, last_evaluated_at FROM signal_rules "
                                "WHERE log_id = ? ORDER BY created_at, rule_id", [log_id]).fetchall()
        finally:
            conn.close()
    return {"rules": [_public(r, c, SignalRule.model_validate_json(d), e) for r, c, d, e in rows],
            "max_rules": MAX_RULES, "signals_execute_actions": False}


def add_rule(log_id: str, spec: Any) -> dict[str, Any]:
    rule = _validate(spec)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            if rule.case_type is not None:
                workspace._case_type(conn, log_id, rule.case_type)
            count = conn.execute("SELECT count(*) FROM signal_rules WHERE log_id = ?", [log_id]).fetchone()[0]
            if count >= MAX_RULES:
                raise workspace.EventLogWorkspaceError(f"a log can hold at most {MAX_RULES} signal rules")
            rule_id = "sig_" + secrets.token_hex(8)
            created = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute("INSERT INTO signal_rules VALUES (?, ?, ?, ?, NULL, NULL)",
                         [log_id, rule_id, created, rule.model_dump_json()])
        finally:
            conn.close()
    return _public(rule_id, created, rule, None)


def delete_rule(log_id: str, rule_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            found = conn.execute("SELECT count(*) FROM signal_rules WHERE log_id = ? AND rule_id = ?",
                                 [log_id, str(rule_id)]).fetchone()[0]
            if not found:
                raise workspace.EventLogWorkspaceError("unknown signal rule")
            conn.execute("DELETE FROM signal_rules WHERE log_id = ? AND rule_id = ?", [log_id, str(rule_id)])
        finally:
            conn.close()
    return {"deleted": True, "rule_id": rule_id}


_PER_CASE = """CREATE OR REPLACE TEMP TABLE sc AS SELECT case_id, count(*) AS events,
    date_diff('second', min(ts), max(ts)) AS duration, count(*) > count(DISTINCT activity) AS rework,
    arg_max(activity, struct_pack(at := ts, event_order := event_id)) AS last_activity,
    min(ts) AS first_ts, max(ts) AS last_ts, list(DISTINCT activity) AS acts
  FROM rc GROUP BY case_id"""


def _measure(conn, when: KpiCondition) -> float | None:
    if when.metric == "case_count":
        return float(conn.execute("SELECT count(*) FROM sc").fetchone()[0])
    if when.metric in {"median_throughput_seconds", "p90_throughput_seconds"}:
        q = 0.5 if when.metric.startswith("median") else 0.9
        value = conn.execute("SELECT quantile_cont(duration, ?) FROM sc", [q]).fetchone()[0]
        return None if value is None else float(value)
    total, hits = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE " + ("rework" if when.metric == "rework_rate" else "duration > ?") + ") FROM sc",
        [] if when.metric == "rework_rate" else [when.sla_seconds]).fetchone()
    return hits / total if total else None


def _matching(conn, when: CaseCondition, as_of: datetime) -> tuple[int, list[dict[str, Any]], list[str]]:
    predicate, params = {
        "duration_over": ("duration > ?", [when.seconds]),
        "contains_activity": ("list_contains(acts, ?)", [when.activity]),
        "lacks_activity": ("NOT list_contains(acts, ?)", [when.activity]),
        "has_rework": ("rework", []),
        "open_longer_than": ("NOT list_contains(?, last_activity) AND epoch(CAST(? AS TIMESTAMP) - first_ts) > ?",
                             [when.end_activities, as_of, when.seconds]),
    }[when.condition]
    total = conn.execute(f"SELECT count(*) FROM sc WHERE {predicate}", params).fetchone()[0]
    order = 'first_ts, case_id' if when.condition == 'open_longer_than' else 'duration DESC, case_id'
    ids = [row[0] for row in conn.execute(
        f"SELECT case_id FROM sc WHERE {predicate} ORDER BY {order} LIMIT ?",
        params + [MAX_REMEMBERED_CASES]).fetchall()]
    evidence = [{"case_id": c, "events": e, "duration_seconds": d, "last_activity": a,
                 "first_event_at": first.isoformat() + "Z", "last_event_at": t.isoformat() + "Z",
                 "age_since_first_event_seconds": (as_of-first).total_seconds(),
                 "idle_since_last_event_seconds": (as_of-t).total_seconds()}
                for c, e, d, a, first, t in conn.execute(
        f"SELECT case_id, events, duration, last_activity, first_ts, last_ts FROM sc WHERE {predicate} "
        f"ORDER BY {order} LIMIT ?", params + [MAX_EVIDENCE_CASES]).fetchall()]
    return total, evidence, ids


def _new_case_comparison(total: int, ids: list[str], previous: dict | None) -> dict[str, Any]:
    """Set-difference bounds; retained samples are never treated as full history."""
    if previous is None:
        return {'count': None, 'lower_bound': None, 'upper_bound': None,
                'comparison_complete': False, 'reason': 'first_check',
                'current_matching_cases': total, 'current_retained_cases': len(ids)}
    before = set(previous.get('case_ids') or [])
    candidate = len(set(ids) - before)
    old_total = previous.get('matching_cases')
    known_total = type(old_total) is int and old_total >= len(before)
    unretained = total - len(ids)
    low = max(0, candidate - (old_total-len(before))) if known_total else 0
    high = candidate + unretained
    complete = low == high
    return {'count': low if complete else None, 'lower_bound': low, 'upper_bound': high,
            'comparison_complete': complete,
            'reason': None if complete else ('retained_case_limit' if known_total else 'previous_population_unknown'),
            'current_matching_cases': total, 'current_retained_cases': len(ids),
            'previous_matching_cases': old_total if known_total else None,
            'previous_retained_cases': len(before)}


def evaluate(log_id: str) -> dict[str, Any]:
    """Evaluate every rule on the log as it is now and remember the result."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT rule_id, created_at, definition, last_state FROM signal_rules "
                                "WHERE log_id = ? ORDER BY created_at, rule_id", [log_id]).fetchall()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            signals = []
            for rule_id, created, definition, last_state in rows:
                rule = SignalRule.model_validate_json(definition)
                previous = json.loads(last_state) if last_state else None
                notion = workspace._case_type(conn, log_id, rule.case_type)
                conn.execute(f"CREATE OR REPLACE TEMP TABLE ce AS {workspace._CASE_EVENTS}", [log_id, notion])
                as_of = conn.execute("SELECT max(ts) FROM ce").fetchone()[0]
                cohorts.restrict(conn, cohorts.parse_cohort(rule.cohort), log_id, table="rc")
                conn.execute(_PER_CASE)
                result: dict[str, Any] = {"rule_id": rule_id, "name": rule.name, "case_notion": notion,
                                          "when": rule.when.model_dump(), "cohort": rule.cohort}
                if isinstance(rule.when, KpiCondition):
                    value = _measure(conn, rule.when)
                    triggered = value is not None and (value > rule.when.threshold if rule.when.operator == "above"
                                                       else value < rule.when.threshold)
                    result.update(value=value, triggered=triggered,
                                  changed_since_last_evaluation=None if previous is None
                                  else previous.get("triggered") != triggered)
                    state = {"triggered": triggered, "value": value}
                else:
                    if isinstance(rule.when, ComplianceCondition):
                        compliance.positions(conn, "rc")
                        summary, ids = compliance.evaluate_rule(conn, rule.when.rule)
                        total, evidence, ids = summary["violating_cases"], summary["violating_case_examples"], ids[:MAX_REMEMBERED_CASES]
                        result["compliance"] = {k: summary[k] for k in (
                            "description", "activated_cases", "violation_rate", "violation_rate_ci95", "unknown_cases")}
                    else:
                        total, evidence, ids = _matching(conn, rule.when, as_of)
                    triggered = total >= rule.when.minimum_cases
                    comparison = _new_case_comparison(total, ids, previous)
                    result.update(matching_cases=total, triggered=triggered, evidence_cases=evidence,
                                  evidence_truncated=total > len(evidence),
                                  new_cases_since_last_evaluation=comparison['count'],
                                  new_cases_comparison=comparison,
                                  as_of=as_of.isoformat() + "Z" if as_of else None)
                    state = {"triggered": triggered, "case_ids": ids, "matching_cases": total}
                conn.execute("UPDATE signal_rules SET last_evaluated_at = ?, last_state = ? WHERE log_id = ? AND rule_id = ?",
                             [now, json.dumps(state), log_id, rule_id])
                signals.append(result)
        finally:
            conn.close()
    return {
        "log": info, "evaluated_at": now.isoformat() + "Z", "signals": signals,
        "triggered": sum(1 for s in signals if s["triggered"]),
        "signals_execute_actions": False,
        "interpretation": ("A triggered signal means the saved log meets the rule now. It is not a verified "
                           "business problem, a cause or an instruction to act."),
        **workspace.DESCRIPTIVE_ONLY,
    }


__all__ = ["add_rule", "delete_rule", "evaluate", "list_rules"]
