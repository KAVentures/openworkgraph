"""Local, frozen prospective triage reviews attached to prediction snapshots."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import re
import secrets

from shared import prospective_triage_policy as core
from . import event_log_prediction_snapshots as snapshots
from . import event_log_predictions as predictions
from . import event_log_workspace as workspace

FORMAT = core.FORMAT
MAX_POLICIES = 12
MAX_PAGE = 100
HEX = re.compile(r"[a-f0-9]{64}\Z")
POLICY = re.compile(r"triage_[a-f0-9]{16}\Z")
FILTERS = {"all","both","model_only","baseline_only","neither","unknown","known_positive",
           "known_negative","missing_or_inconsistent","excluded"}
CLAIMS = {"local_only": True, "intervention_executed": False, "causal_effect_verified": False,
          "actual_savings_verified": False, "execution_authorized": False,
          "source_completeness_verified": False, "future_performance_verified": False}


def _fail(message):
    return workspace.EventLogWorkspaceError(message)


def _match(value, regex):
    return isinstance(value,str) and regex.fullmatch(value) is not None


def _spec(value):
    try:
        return core.parse(value)
    except core.PolicyError as exc:
        raise _fail(str(exc)) from None


def _frozen(log_id,snapshot_id):
    row = snapshots._row(log_id,snapshot_id)
    sid, created, as_of, settings_text, summary_text, cases_text = row
    try:
        settings = predictions.parse(json.loads(settings_text))
        summary, cases = json.loads(summary_text), json.loads(cases_text)
        if (not isinstance(summary,dict) or not isinstance(cases,list) or
                summary.get("predicted_cases") != len(cases) or
                type(summary.get("open_cases")) is not int or summary["open_cases"] < len(cases)):
            raise ValueError
        pin = core.digest([FORMAT,log_id,*row])
        at = datetime.fromisoformat(as_of.removesuffix("Z"))
        if at.tzinfo is not None:
            raise ValueError
    except (ValueError,TypeError,KeyError,AttributeError):
        raise _fail("invalid frozen prediction snapshot") from None
    return {"sid":sid,"created":created,"as_of":as_of,"at":at,"settings":settings,
            "summary":summary,"cases":cases,"sha256":pin}


def _plan(frozen,spec):
    try:
        return core.plan(frozen["cases"],spec,frozen_outcome_available=frozen["settings"].outcome is not None)
    except core.PolicyError as exc:
        raise _fail(str(exc)) from None


def _observe(log_id,frozen):
    settings = frozen["settings"]
    ids = [row["case_id"] for row in frozen["cases"]]
    def run(conn):
        as_of = predictions._prepare(conn,settings,log_id)
        expression, params = predictions._outcome_sql(settings.outcome)
        rows = conn.execute(f"""SELECT c.case_id,c.complete,epoch(c.ended-?) AS remaining,
            {expression},c.last_activity,least(c.events,?) AS step
            FROM pc c WHERE c.case_id IN (SELECT unnest(?)) ORDER BY c.case_id""",
            [frozen["at"],*params,predictions.MAX_STEP,ids]).fetchall()
        return {"_observed_at":as_of,"_observed_rows":rows}
    result = workspace._query(log_id,settings.case_type,run)
    at = result["_observed_at"]
    valid = at is not None and at >= frozen["at"]
    since = (at-frozen["at"]).total_seconds() if valid else 0
    rows = result["_observed_rows"]
    return {"at":at,"valid":valid,"since":since,
            "events":result["log"]["events"],
            "current":{r[0]:(r[1],r[2],r[3]) for r in rows},
            "prefixes":{r[0]:(r[1],r[4],r[5]) for r in rows}}


def _require_pre_observation(frozen, observation):
    # Fail closed for older snapshots without the retained raw-event count,
    # any append that added events, changed visible horizon or changed frozen
    # open-prefix membership. This is not a full historical content attestation.
    if (type(frozen["summary"].get("events")) is not int or
            observation["events"] != frozen["summary"]["events"] or
            observation["at"] != frozen["at"] or
            len(observation["prefixes"]) != len(frozen["cases"])):
        raise _fail("snapshot observation changed; take a new prediction snapshot before freezing a policy")
    for row in frozen["cases"]:
        if observation["prefixes"].get(row["case_id"]) != (
                False,row["last_activity"],row["events_so_far"]):
            raise _fail("snapshot prefix changed; take a new prediction snapshot before freezing a policy")


def _plan_summary(frozen,plan,spec):
    return {"format":FORMAT,"snapshot_id":frozen["sid"],"snapshot_sha256":frozen["sha256"],
            "as_of":frozen["as_of"],"policy":spec.model_dump(),
            "policy_sha256":core.digest([FORMAT,frozen["sha256"],spec.model_dump(),plan["ranking_sha256"]]),
            "ranking_sha256":plan["ranking_sha256"],"snapshot_open_cases":frozen["summary"]["open_cases"],
            "snapshot_unsupported_open_cases":frozen["summary"]["open_cases"]-plan["population"],
            "paired_estimate_excluded_cases":len(plan["excluded"]),
            "paired_eligible_cases":plan["eligible"],"effective_capacity":plan["effective_capacity"],
            "requested_capacity":plan["requested_capacity"],"assignment_counts":plan["assignment_counts"],
            "model_cutoff_score":plan["model_cutoff_score"],"baseline_cutoff_score":plan["baseline_cutoff_score"],
            "model_cutoff_ties":plan["model_cutoff_ties"],"baseline_cutoff_ties":plan["baseline_cutoff_ties"],
            **CLAIMS}


def preview(log_id,snapshot_id,payload):
    spec = _spec(payload)
    with workspace._LOCK,snapshots._LOCK:
        frozen = _frozen(log_id,snapshot_id)
        plan = _plan(frozen,spec)
        obs = _observe(log_id,frozen)
        _require_pre_observation(frozen,obs)
        return {**_plan_summary(frozen,plan,spec),"saved":False,
                "interpretation":"Same-capacity deterministic model and naive rankings; no outcome is used to select cases."}


def freeze(log_id,snapshot_id,payload):
    if (not isinstance(payload,dict) or set(payload)!={"policy","expected_policy_sha256","confirm_freeze"} or
            payload["confirm_freeze"] is not True or not _match(payload["expected_policy_sha256"],HEX)):
        raise _fail("explicit freeze confirmation and exact preview digest required")
    spec = _spec(payload["policy"])
    with workspace._LOCK,snapshots._LOCK:
        frozen = _frozen(log_id,snapshot_id)
        plan = _plan(frozen,spec)
        _require_pre_observation(frozen,_observe(log_id,frozen))
        summary = _plan_summary(frozen,plan,spec)
        if summary["policy_sha256"] != payload["expected_policy_sha256"]:
            raise _fail("policy preview changed; prepare and review the current snapshot again")
        with closing(snapshots._connect()) as db,db:
            existing = db.execute("SELECT policy_id,created_at FROM triage_policies WHERE log_id=? AND snapshot_id=? AND policy_sha256=?",
                                  [log_id,snapshot_id,summary["policy_sha256"]]).fetchone()
            if existing:
                return {**summary,"policy_id":existing[0],"created_at":existing[1],"saved":True,"already_saved":True}
            if db.execute("SELECT count(*) FROM triage_policies WHERE log_id=? AND snapshot_id=?",
                          [log_id,snapshot_id]).fetchone()[0] >= MAX_POLICIES:
                raise _fail("prediction snapshot policy limit reached")
            policy_id = "triage_"+secrets.token_hex(8)
            created = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO triage_policies VALUES (?,?,?,?,?,?,?)",
                       [policy_id,log_id,snapshot_id,created,frozen["sha256"],summary["policy_sha256"],
                        json.dumps(spec.model_dump(),ensure_ascii=True,sort_keys=True)])
        return {**summary,"policy_id":policy_id,"created_at":created,"saved":True,"already_saved":False}


def _stored(log_id,snapshot_id,policy_id):
    if not _match(policy_id,POLICY):
        raise _fail("unknown triage policy")
    with closing(snapshots._connect()) as db:
        row = db.execute("SELECT created_at,snapshot_sha256,policy_sha256,definition FROM triage_policies "
                         "WHERE log_id=? AND snapshot_id=? AND policy_id=?",
                         [log_id,snapshot_id,policy_id]).fetchone()
    if row is None:
        raise _fail("unknown triage policy")
    try:
        spec = _spec(json.loads(row[3]))
    except (ValueError,TypeError):
        raise _fail("stored triage policy is invalid") from None
    return row[0],row[1],row[2],spec


def _resolve(log_id,snapshot_id,policy_id):
    frozen = _frozen(log_id,snapshot_id)
    created,pin,policy_pin,spec = _stored(log_id,snapshot_id,policy_id)
    plan = _plan(frozen,spec)
    summary = _plan_summary(frozen,plan,spec)
    if pin != frozen["sha256"] or policy_pin != summary["policy_sha256"]:
        raise _fail("stored prediction or policy integrity changed")
    return frozen,plan,spec,{**summary,"policy_id":policy_id,"created_at":created,"saved":True}


def listing(log_id,snapshot_id):
    with workspace._LOCK,snapshots._LOCK:
        workspace._query(log_id,None,lambda _: {})
        _frozen(log_id,snapshot_id)
        with closing(snapshots._connect()) as db:
            rows = db.execute("SELECT policy_id,created_at,policy_sha256,definition FROM triage_policies "
                              "WHERE log_id=? AND snapshot_id=? ORDER BY created_at DESC,policy_id",
                              [log_id,snapshot_id]).fetchall()
        if len(rows)>MAX_POLICIES:
            raise _fail("stored triage policy limit exceeded")
        items=[]
        for policy_id,created,pin,definition in rows:
            try:
                spec=_spec(json.loads(definition))
                items.append({"policy_id":policy_id,"created_at":created,"policy_sha256":pin,
                              "policy":spec.model_dump()})
            except (ValueError,TypeError):
                items.append({"policy_id":policy_id,"created_at":created,"policy_sha256":pin,
                              "policy":None,"integrity_unavailable":True})
        return {"format":FORMAT,"snapshot_id":snapshot_id,"policies":items,"max_policies":MAX_POLICIES,**CLAIMS}


def delete(log_id,snapshot_id,policy_id,payload):
    if (not isinstance(payload,dict) or set(payload)!={"expected_policy_sha256","confirm_delete"} or
            payload["confirm_delete"] is not True or not _match(payload["expected_policy_sha256"],HEX)):
        raise _fail("explicit policy deletion and exact digest required")
    with workspace._LOCK,snapshots._LOCK:
        workspace._query(log_id,None,lambda _: {})
        with closing(snapshots._connect()) as db,db:
            changed=db.execute("DELETE FROM triage_policies WHERE log_id=? AND snapshot_id=? AND policy_id=? AND policy_sha256=?",
                               [log_id,snapshot_id,policy_id,payload["expected_policy_sha256"]]).rowcount
            if changed!=1:
                raise _fail("policy changed or unavailable; reload the inventory")
        return {"deleted":True,"policy_id":policy_id,**CLAIMS}


def _report(log_id,snapshot_id,policy_id):
    frozen,plan,spec,summary = _resolve(log_id,snapshot_id,policy_id)
    observation = _observe(log_id,frozen)
    scored = core.evaluate(plan,observation["current"],horizon_valid=observation["valid"],
                           since=observation["since"],spec=spec)
    rows=scored.pop("rows")
    # Only current labels, not raw events or mutable process text, bind the page.
    # Changing the horizon also invalidates old evidence even if labels match.
    pin=core.digest([FORMAT,summary["policy_sha256"],snapshots._iso(observation["at"]),
                     observation["events"],[(r["case_id"],r["observation"],r["label"]) for r in rows]])
    report={**summary,**scored,"evaluation_sha256":pin,
            "evaluated_against":snapshots._iso(observation["at"]),
            "seconds_since_snapshot":observation["since"],
            "observation_horizon_valid":observation["valid"],"current_retained_events":observation["events"],
            "identification_bounds_not_confidence_intervals":True,
            "interpretation":"Frozen equal-capacity rankings compared on recorded outcomes. Unknown outcomes are not treated as negatives. "
                             "Error costs are declared abstract weights, not currency, actual savings or treatment effects. "
                             "No intervention is performed or recommended. Finished-case observations and censoring rely on retained-log completeness."}
    return report,rows,plan["excluded"]


def evaluate(log_id,snapshot_id,policy_id):
    with workspace._LOCK,snapshots._LOCK:
        report,_,_=_report(log_id,snapshot_id,policy_id)
        return report


def evidence(log_id,snapshot_id,policy_id,payload):
    if (not isinstance(payload,dict) or set(payload)!={"expected_evaluation_sha256","filter","offset","limit"} or
            not _match(payload["expected_evaluation_sha256"],HEX) or
            not isinstance(payload["filter"],str) or payload["filter"] not in FILTERS or
            type(payload["offset"]) is not int or not 0<=payload["offset"]<=core.MAX_CASES or
            type(payload["limit"]) is not int or not 1<=payload["limit"]<=MAX_PAGE):
        raise _fail("invalid exact triage evidence page")
    with workspace._LOCK,snapshots._LOCK:
        report,rows,excluded=_report(log_id,snapshot_id,policy_id)
        if report["evaluation_sha256"] != payload["expected_evaluation_sha256"]:
            raise _fail("prediction evaluation changed; score the policy again before browsing evidence")
        kind=payload["filter"]
        if kind=="excluded":
            matched=excluded
        elif kind=="all":
            matched=rows
        elif kind=="unknown":
            matched=[r for r in rows if r["label"] is None]
        elif kind=="known_positive":
            matched=[r for r in rows if r["label"]==1]
        elif kind=="known_negative":
            matched=[r for r in rows if r["label"]==0]
        elif kind=="missing_or_inconsistent":
            matched=[r for r in rows if r["observation"] in {"missing","inconsistent"}]
        else:
            matched=[r for r in rows if r["assignment"]==kind]
        start,limit=payload["offset"],payload["limit"]
        page=matched[start:start+limit]
        return {"format":FORMAT,"evaluation_sha256":report["evaluation_sha256"],"filter":kind,
                "matched_cases":len(matched),"offset":start,"limit":limit,
                "next_offset":start+len(page) if page and start+len(page)<len(matched) else None,
                "rows":page,**CLAIMS}
