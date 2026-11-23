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

from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_compliance as compliance
from . import event_log_workspace as workspace
from . import event_log_signal_inbox as signal_inbox
from . import event_log_signal_transitions as signal_transitions

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


class ConformanceCondition(_Strict):
    """Adherence to a frozen reference net (PNML) saved with the rule: the share of cases that do not fit it,
    or their alignment fitness, optionally over cases started in the last ``recent_days`` of the log."""
    kind: Literal["conformance"]
    net_name: Label
    pnml: str = Field(min_length=20, max_length=60000)
    metric: Literal["non_fitting_share", "alignment_fitness"]
    operator: Literal["above", "below"]
    threshold: Union[StrictInt, StrictFloat] = Field(ge=0, le=1)
    recent_days: StrictInt | None = Field(default=None, ge=1, le=3650)
    minimum_cases: StrictInt = Field(default=1, ge=1, le=10 ** 9)


class DueDateCondition(_Strict):
    """Due-date adherence of an activity (see event_log_due_dates): open cases now overdue, or the late
    share among cases where it happened."""
    kind: Literal["due_date"]
    activity: Label
    due_attribute: Label | None = None
    due_days: Union[StrictInt, StrictFloat, None] = Field(default=None, gt=0, le=3650)
    metric: Literal["open_overdue_cases", "late_share"]
    operator: Literal["above", "below"] = "above"
    threshold: Union[StrictInt, StrictFloat] = Field(ge=0)
    minimum_cases: StrictInt = Field(default=1, ge=1, le=10 ** 9)


class SignalRule(_Strict):
    name: str = Field(min_length=1, max_length=120)
    case_type: Label | None = None
    cohort: dict[str, Any] = Field(default_factory=dict)
    when: Annotated[Union[KpiCondition, CaseCondition, ComplianceCondition, ConformanceCondition,
                          DueDateCondition], Field(discriminator="kind")]


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
    elif isinstance(when, DueDateCondition):
        if (when.due_attribute is None) == (when.due_days is None):
            raise workspace.EventLogWorkspaceError("a due-date signal needs either due_attribute or due_days")
        if when.metric == "late_share" and when.threshold > 1:
            raise workspace.EventLogWorkspaceError("late_share is a fraction between 0 and 1")
    elif isinstance(when, ConformanceCondition):
        from . import event_log_alignments as alignments
        alignments.reference_net(when.pnml.encode())
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
            conn.execute("DELETE FROM signal_snoozes WHERE log_id = ? AND rule_id = ?", [log_id, str(rule_id)])
            conn.execute("DELETE FROM signal_reviews WHERE log_id = ? AND rule_id = ?", [log_id, str(rule_id)])
            conn.execute("DELETE FROM signal_transitions WHERE log_id = ? AND rule_id = ?", [log_id, str(rule_id)])
            conn.execute("DELETE FROM signal_rules WHERE log_id = ? AND rule_id = ?", [log_id, str(rule_id)])
        finally:
            conn.close()
    return {"deleted": True, "rule_id": rule_id}


_PER_CASE = """CREATE OR REPLACE TEMP TABLE sc AS SELECT case_id, count(*) AS events,
    epoch(max(ts) - min(ts)) AS duration, count(*) > count(DISTINCT activity) AS rework,
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


def _evaluate_rule(conn, log_id: str, rule: SignalRule) -> tuple[dict[str, Any], dict[str, Any]]:
    notion = workspace._case_type(conn, log_id, rule.case_type)
    conn.execute(f"CREATE OR REPLACE TEMP TABLE ce AS {workspace._CASE_EVENTS}", [log_id, notion])
    as_of = conn.execute("SELECT max(ts) FROM ce").fetchone()[0]
    cohorts.restrict(conn, cohorts.parse_cohort(rule.cohort), log_id, table="rc")
    conn.execute(_PER_CASE)
    result: dict[str, Any] = {"name": rule.name, "case_notion": notion,
                              "when": rule.when.model_dump(exclude={"pnml"}), "cohort": rule.cohort}
    if isinstance(rule.when, DueDateCondition):
        from . import event_log_due_dates as due_dates
        w = rule.when
        got = due_dates.collect(conn, log_id, w.activity, w.due_attribute, table="rc")
        groups, _, _, _, _ = due_dates.classify(got["cases_rows"], got["raw_due"], got["log_end"],
                                                due_days=w.due_days, early_days=None)
        done = groups["on_time"] + groups["early"] + groups["late"]
        value = (groups["open_overdue"] if w.metric == "open_overdue_cases"
                 else (round(groups["late"] / done, 4) if done else None))
        basis = groups["open_overdue"] + groups["open_not_due"] if w.metric == "open_overdue_cases" else done
        triggered = basis >= w.minimum_cases and value is not None and (
            value > w.threshold if w.operator == "above" else value < w.threshold)
        result.update(value=value, triggered=triggered, due_dates={k: groups[k] for k in (
                          "on_time", "early", "late", "open_overdue", "open_not_due", "no_due_date",
                          "unreadable_due_date")},
                      as_of=as_of.isoformat() + "Z" if as_of else None)
        state = {"triggered": triggered, "value": value}
    elif isinstance(rule.when, ConformanceCondition):
        from . import event_log_alignments as alignments
        since = None
        if rule.when.recent_days and as_of is not None:
            since = as_of - timedelta(days=rule.when.recent_days)
        paths = conn.execute("""SELECT path, count(*) FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path, min(ts) AS started FROM rc GROUP BY case_id)
            WHERE ? IS NULL OR started >= ? GROUP BY path""", [since, since]).fetchall()
        measured = alignments.conformance_of(rule.when.pnml.encode(), paths)
        value = measured[rule.when.metric]
        enough = measured["cases"] >= rule.when.minimum_cases
        triggered = enough and value is not None and (
            value > rule.when.threshold if rule.when.operator == "above" else value < rule.when.threshold)
        result.update(value=value, triggered=triggered, conformance=measured,
                      as_of=as_of.isoformat() + "Z" if as_of else None)
        state = {"triggered": triggered, "value": value}
    elif isinstance(rule.when, KpiCondition):
        value = _measure(conn, rule.when)
        triggered = value is not None and (value > rule.when.threshold if rule.when.operator == "above"
                                           else value < rule.when.threshold)
        result.update(value=value, triggered=triggered)
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
        result.update(matching_cases=total, triggered=triggered, evidence_cases=evidence,
                      evidence_truncated=total > len(evidence),
                      as_of=as_of.isoformat() + "Z" if as_of else None)
        state = {"triggered": triggered, "case_ids": ids, "matching_cases": total}
    return result, state


def preview_rule(log_id: str, spec: Any) -> dict[str, Any]:
    """Run a draft rule against the current log without reading or saving prior signal state."""
    rule = _validate(spec)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            result, _ = _evaluate_rule(conn, log_id, rule)
            cohort_cases = conn.execute("SELECT count(*) FROM sc").fetchone()[0]
        finally:
            conn.close()
    return {"signal": result, "cohort_cases": cohort_cases, "signals_execute_actions": False,
            **workspace.DESCRIPTIVE_ONLY}


def evaluate(log_id: str, *, rule_id: str | None = None) -> dict[str, Any]:
    """Record the current result for one saved rule, or every rule on the log."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            params = [log_id]
            rule_filter = ""
            if rule_id is not None:
                rule_filter = " AND rule_id = ?"
                params.append(rule_id)
            rows = conn.execute("SELECT rule_id, definition, last_state FROM signal_rules "
                                f"WHERE log_id = ?{rule_filter} ORDER BY created_at, rule_id", params).fetchall()
            if rule_id is not None and not rows:
                raise workspace.EventLogWorkspaceError("unknown signal rule")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            log_basis = signal_inbox.log_basis(conn, log_id)
            signals = []
            recorded_transitions = False
            conn.execute("BEGIN")
            for saved_id, definition, last_state in rows:
                rule = SignalRule.model_validate_json(definition)
                previous = json.loads(last_state) if last_state else None
                result, state = _evaluate_rule(conn, log_id, rule)
                result["rule_id"] = saved_id
                if "matching_cases" in state:
                    comparison = _new_case_comparison(state["matching_cases"], state["case_ids"], previous)
                    result.update(new_cases_since_last_evaluation=comparison["count"], new_cases_comparison=comparison)
                else:
                    result["changed_since_last_evaluation"] = (None if previous is None
                                                               else previous.get("triggered") != state["triggered"])
                # Preserve exact source revision so unchanged metrics after an
                # append still require explicit reevaluation and fresh review.
                state["log_basis"] = log_basis
                # A new state is a new review event even if it later cycles
                # back to an old value. Do not resurrect an obsolete approval.
                if previous is None or previous != state:
                    conn.execute("DELETE FROM signal_snoozes WHERE log_id=? AND rule_id=?", [log_id, saved_id])
                    conn.execute("DELETE FROM signal_reviews WHERE log_id=? AND rule_id=?", [log_id, saved_id])
                conn.execute("UPDATE signal_rules SET last_evaluated_at = ?, last_state = ? WHERE log_id = ? AND rule_id = ?",
                             [now, json.dumps(state), log_id, saved_id])
                if signal_transitions.append(conn, log_id, saved_id, previous, state, now):
                    recorded_transitions = True
                signals.append(result)
            if recorded_transitions:
                signal_transitions.trim(conn, log_id)
            conn.execute("COMMIT")
        except Exception:
            # A failed evaluation must not leave half-updated rule states or
            # transition receipts, including the case of a failed later rule.
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
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


MAX_SCORECARD_MONTHS = 24


def _value(conn, log_id: str, when: Any) -> float | None:
    """The measured value of a KPI, conformance or due-date condition over the cases in ``rc``/``sc``."""
    if isinstance(when, KpiCondition):
        return _measure(conn, when)
    if isinstance(when, ConformanceCondition):
        from . import event_log_alignments as alignments
        paths = conn.execute("""SELECT path, count(*) FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM rc GROUP BY case_id) GROUP BY path""").fetchall()
        return alignments.conformance_of(when.pnml.encode(), paths)[when.metric] if paths else None
    if isinstance(when, DueDateCondition):
        from . import event_log_due_dates as due_dates
        got = due_dates.collect(conn, log_id, when.activity, when.due_attribute, table="rc")
        groups = due_dates.classify(got["cases_rows"], got["raw_due"], got["log_end"],
                                    due_days=when.due_days, early_days=None)[0]
        if when.metric == "open_overdue_cases":
            return float(groups["open_overdue"])
        done = groups["on_time"] + groups["early"] + groups["late"]
        return round(groups["late"] / done, 4) if done else None
    return None


def scorecard(log_id: str, months: int = 6) -> dict[str, Any]:
    """Each value-based signal (KPI, conformance, due date) month by month, by when cases started, with
    whether its condition held that month. Read-only: it records no state and never changes a review."""
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= MAX_SCORECARD_MONTHS:
        raise workspace.EventLogWorkspaceError(f"months must be 1 to {MAX_SCORECARD_MONTHS}")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            rules = conn.execute("SELECT rule_id, definition FROM signal_rules WHERE log_id = ? "
                                 "ORDER BY created_at, rule_id", [log_id]).fetchall()
            rows, month_keys = [], None
            for rule_id, definition in rules:
                rule = SignalRule.model_validate_json(definition)
                if not isinstance(rule.when, (KpiCondition, ConformanceCondition, DueDateCondition)):
                    continue
                notion = workspace._case_type(conn, log_id, rule.case_type)
                conn.execute(f"CREATE OR REPLACE TEMP TABLE ce AS {workspace._CASE_EVENTS}", [log_id, notion])
                cohorts.restrict(conn, cohorts.parse_cohort(rule.cohort), log_id, table="rc_all")
                starts = conn.execute("""SELECT strftime(date_trunc('month', started), '%Y-%m') AS m, count(*)
                    FROM (SELECT case_id, min(ts) AS started FROM rc_all GROUP BY case_id) GROUP BY m ORDER BY m""").fetchall()
                keys = [m for m, _ in starts][-months:]
                month_keys = sorted(set(month_keys or []) | set(keys))[-months:]
                values = []
                for month, cases in starts:
                    if month not in keys:
                        continue
                    conn.execute("""CREATE OR REPLACE TEMP TABLE rc AS SELECT * FROM rc_all WHERE case_id IN (
                        SELECT case_id FROM rc_all GROUP BY case_id
                        HAVING strftime(date_trunc('month', min(ts)), '%Y-%m') = ?)""", [month])
                    conn.execute(_PER_CASE)
                    value = _value(conn, log_id, rule.when)
                    holds = None if value is None else (value > rule.when.threshold if rule.when.operator == "above"
                                                        else value < rule.when.threshold)
                    values.append({"month": month, "cases": cases, "value": value, "holds": holds})
                when = {k: v for k, v in rule.when.model_dump().items() if k != "pnml"}
                rows.append({"rule_id": rule_id, "name": rule.name, "when": when, "months": values})
        finally:
            conn.close()
    return {
        "log": info, "months": month_keys or [], "rows": rows,
        "skipped_kinds": "case-matching and compliance signals count matching cases now and are not shown by month",
        "interpretation": ("Each month holds the cases that started in it. A month where a signal holds met the rule's "
                           "condition then; recent months may still have open cases."),
        **workspace.DESCRIPTIVE_ONLY,
    }


__all__ = ["add_rule", "delete_rule", "evaluate", "list_rules", "preview_rule", "scorecard"]
