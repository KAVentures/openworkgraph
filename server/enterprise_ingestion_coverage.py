"""Durable, compact coverage of locally committed ingestion request windows.

Coverage describes admitted request intervals that reached the retained
checkpoint transaction. It is not coverage of event occurrence time, an
upstream completeness guarantee, or proof of a historical source snapshot.
"""
from datetime import timedelta
import json
from pydantic import Field, StrictInt, ValidationError, model_validator

from . import enterprise_rest_feed as rest
from . import event_log_workspace as ws

FORMAT = "owg_enterprise_committed_windows_v1"
MAX_INTERVALS = 128
MAX_TARGETS = 20
MAX_REVIEW_DAYS = 366
SCHEMA = """CREATE TABLE IF NOT EXISTS enterprise_ingestion_coverage (
    profile_id VARCHAR PRIMARY KEY, definition VARCHAR NOT NULL
)"""


def init(c):
    c.execute(SCHEMA)


def exists(c):
    return bool(c.execute("""SELECT 1 FROM information_schema.tables
        WHERE table_name='enterprise_ingestion_coverage'""").fetchone())


def erase(c, profile_id):
    if exists(c):
        c.execute("DELETE FROM enterprise_ingestion_coverage WHERE profile_id=?", [profile_id])


def microseconds(a, b):
    # Avoid floating point rounding in all interval arithmetic.
    delta = b-a
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if start >= end:
            raise ValueError("invalid coverage interval")
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def encode(intervals):
    return [[rest._iso(a), rest._iso(b)] for a,b in intervals]


def decode(intervals):
    if not isinstance(intervals, list) or len(intervals) > MAX_INTERVALS:
        raise ValueError("invalid coverage population")
    values = [(rest._stamp(a),rest._stamp(b)) for a,b in intervals]
    if union(values) != values:
        raise ValueError("noncanonical coverage")
    return values


def empty():
    return {"intervals": [], "recorded_commits": 0, "discarded_disjoint_intervals": 0,
            "first_recorded_at": None, "last_recorded_at": None}


def load(c, profile_id):
    row = c.execute("SELECT definition FROM enterprise_ingestion_coverage WHERE profile_id=?",
                    [profile_id]).fetchone()
    if not row:
        return empty()
    value = json.loads(row[0])
    decode(value["intervals"])
    for key in ("recorded_commits","discarded_disjoint_intervals"):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError("invalid coverage counter")
    return value


def commit(c, p, until):
    """Called in the same transaction as append/import/checkpoint and receipt.

    Use the actual admitted request interval from the journal, not an
    inference from the previous checkpoint or current tuning configuration.
    A legacy in-flight intent without a journal row creates no coverage.
    """
    row = c.execute("""SELECT since_at,until_at,checkpoint_before,revision,state
        FROM enterprise_ingestion_runs WHERE profile_id=? AND run_id=?""",
        [p["profile_id"],p["run_id"]]).fetchone()
    if row is None:
        return
    since, admitted_until, prior, revision, state = row
    if (state!="running" or revision!=p["revision"] or prior!=p["checkpoint"]
            or rest._stamp(admitted_until)!=until):
        raise rest.FeedError("coverage_admitted_window_mismatch")
    start = rest._stamp(since)
    anchor = rest._stamp(p["checkpoint"] or p["config"]["initial_since"])
    if not start<=anchor<until:
        raise rest.FeedError("coverage_admitted_window_invalid")
    value = load(c,p["profile_id"])
    intervals = union([*decode(value["intervals"]),(start,until)])
    dropped = max(0,len(intervals)-MAX_INTERVALS)
    if dropped:
        intervals = intervals[dropped:]
    at = rest._iso(rest._now())
    value.update(intervals=encode(intervals),recorded_commits=value["recorded_commits"]+1,
                 discarded_disjoint_intervals=value["discarded_disjoint_intervals"]+dropped,
                 first_recorded_at=value["first_recorded_at"] or at,last_recorded_at=at)
    c.execute("INSERT OR REPLACE INTO enterprise_ingestion_coverage VALUES (?,?)",
              [p["profile_id"],json.dumps(value,separators=(",",":"))])


class Target(rest.Strict):
    profile_id: str = Field(pattern=r"^rest_[a-f0-9]{24}$")
    revision: StrictInt = Field(ge=1)


class Review(rest.Strict):
    targets: list[Target] = Field(min_length=1,max_length=MAX_TARGETS)
    since: str = Field(min_length=20,max_length=64)
    until: str = Field(min_length=20,max_length=64)

    @model_validator(mode="after")
    def unique(self):
        if len({x.profile_id for x in self.targets}) != len(self.targets):
            raise ValueError("duplicate target")
        return self


def clipped(intervals,start,end):
    return [(max(a,start),min(b,end)) for a,b in intervals if a<end and b>start]


def partition(intervals,start,end,initial,checkpoint):
    """Partition a requested range into disjoint exact half-open segments."""
    boundaries={start,end}
    for t in (initial,checkpoint):
        if t is not None and start<t<end:
            boundaries.add(t)
    observed=clipped(intervals,start,end)
    for a,b in observed:
        boundaries.update((a,b))
    points=sorted(boundaries)
    out=[]
    cursor=0
    for a,b in zip(points,points[1:]):
        while cursor<len(observed) and observed[cursor][1]<=a:
            cursor+=1
        covered=cursor<len(observed) and observed[cursor][0]<=a and b<=observed[cursor][1]
        if covered:
            kind="committed_request_window"
        elif a<initial:
            kind="before_configured_anchor"
        elif checkpoint is not None and b<=checkpoint:
            kind="unknown_prior_coverage"
        else:
            kind="not_yet_checkpointed"
        if out and out[-1]["kind"]==kind:
            out[-1]["until"]=rest._iso(b)
            out[-1]["microseconds"]+=microseconds(a,b)
        else:
            out.append({"kind":kind,"since":rest._iso(a),"until":rest._iso(b),
                        "microseconds":microseconds(a,b)})
    return out


KINDS=("committed_request_window","before_configured_anchor",
       "unknown_prior_coverage","not_yet_checkpointed")


def backlog(p, now):
    """Clock-based planning estimate only, never provider eligibility."""
    anchor=rest._stamp(p["checkpoint"] or p["config"]["initial_since"])
    window=p["config"]["window_hours"]
    lag=p["config"].get("ingestion_lag_minutes")
    # SAP uses completed whole-day windows, and Google has application-specific
    # cutoff semantics. Do not claim this is their precise next eligible window.
    reference=now-timedelta(minutes=lag or 0)
    outstanding=max(0,microseconds(anchor,reference))
    window_us=window*3600*1000000
    return {"anchor":rest._iso(anchor),"planning_reference":rest._iso(reference),
            "uncheckpointed_microseconds":outstanding,
            "estimated_advancing_windows":(outstanding+window_us-1)//window_us,
            "window_hours":window,"overlap_minutes":p["config"]["overlap_minutes"],
            "configured_reporting_lag_minutes":lag,
            "exact_provider_cutoff_verified":False,
            "estimate_excludes_rate_limits_failures_and_source_density":True}


def inspect_profile(c,p,start,end,now):
    value=load(c,p["profile_id"])
    intervals=decode(value["intervals"])
    initial=rest._stamp(p["config"]["initial_since"])
    checkpoint=rest._stamp(p["checkpoint"]) if p["checkpoint"] else None
    consistent=not intervals or (checkpoint is not None and intervals[-1][1]<=checkpoint)
    if p["status"]=="dataset_deleted":
        consistent=consistent and not intervals
    # A corrupt or contradictory interval index must not be interpreted as
    # committed coverage. Report it separately and exclude it from summaries.
    segments=partition(intervals if consistent else [],start,end,initial,checkpoint)
    totals={k:0 for k in KINDS}
    for segment in segments:
        totals[segment["kind"]]+=segment["microseconds"]
    return {"profile_id":p["profile_id"],"provider":p.get("provider","rest"),
            "revision":p["revision"],"enabled":p["enabled"],"status":p["status"],
            "checkpoint":p["checkpoint"],"initial_since":p["config"]["initial_since"],
            "retained_dataset_present":p["log_id"] is not None,
            "integrity_hold":bool(p.get("integrity_hold")),
            "coverage_index_consistent":consistent,
            "recorded_commits":value["recorded_commits"],
            "discarded_disjoint_intervals":value["discarded_disjoint_intervals"],
            "first_recorded_at":value["first_recorded_at"],"last_recorded_at":value["last_recorded_at"],
            "retained_interval_count":len(intervals),"segments":segments,"microseconds_by_kind":totals,
            "backlog":backlog(p,now),"provider_completeness_verified":False,
            "event_occurrence_coverage_verified":False}


def inventory():
    with ws._LOCK:
        c=rest._connect()
        try:
            rows=[]
            for (pid,) in c.execute("SELECT profile_id FROM enterprise_rest_profiles ORDER BY profile_id").fetchall():
                p=rest._get(c,pid)
                rows.append({"profile_id":pid,"provider":p.get("provider","rest"),"revision":p["revision"],
                             "enabled":p["enabled"],"checkpoint":p["checkpoint"],
                             "initial_since":p["config"]["initial_since"]})
            return {"format":FORMAT,"profiles":rows,"max_targets":MAX_TARGETS,
                    "max_review_days":MAX_REVIEW_DAYS,"provider_reads_performed":False}
        finally:
            c.close()


def review(value,*,now=None):
    try:
        spec=Review.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_coverage_review",422) from None
    now=now or rest._now()
    start,end=rest._stamp(spec.since),rest._stamp(spec.until)
    if not start<end<=now or end-start>timedelta(days=MAX_REVIEW_DAYS):
        raise rest.FeedError("invalid_coverage_review_range",422)
    with ws._LOCK:
        c=rest._connect()
        try:
            rows=[]
            for target in sorted(spec.targets,key=lambda t:t.profile_id):
                p=rest._get(c,target.profile_id)
                if p["revision"]!=target.revision:
                    raise rest.FeedError("profile_changed")
                try:
                    row=inspect_profile(c,p,start,end,now)
                except (ValueError,TypeError,KeyError,rest.FeedError):
                    # Never synthesize coverage from malformed persisted state.
                    row={"profile_id":p["profile_id"],"provider":p.get("provider","rest"),
                         "revision":p["revision"],"coverage_index_consistent":False,
                         "error_code":"invalid_coverage_index","segments":[],"microseconds_by_kind":None}
                rows.append(row)
        finally:
            c.close()
    included=[r for r in rows if r["coverage_index_consistent"]]
    totals={k:sum(r["microseconds_by_kind"][k] for r in included) for k in KINDS}
    denominator=microseconds(start,end)*len(included)
    return {"format":FORMAT,"observed_at":rest._iso(now),"since":rest._iso(start),"until":rest._iso(end),
            "profiles":rows,"summary":{"selected_profiles":len(rows),"included_profiles":len(included),
                "excluded_inconsistent_profiles":len(rows)-len(included),
                "profile_window_microseconds":denominator,
                "microseconds_by_kind":totals,
                "committed_request_window_fraction":totals[KINDS[0]]/denominator if denominator else None},
            "provider_reads_performed":False,"credential_refresh_performed":False,
            "execution_authorized":False,"provider_completeness_verified":False,
            "event_occurrence_coverage_verified":False,"pre_upgrade_history_reconstructed":False,
            "interpretation":"Union of locally committed request intervals only; unknown is not a proven gap in source events."}
