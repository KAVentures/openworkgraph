"""Opt-in local ingestion service-level policies and durable alert episodes.

No credential access, source dispatch, remote notification or live-health claim.
All decisions use only the retained profile and its admitted run journal.
"""
from datetime import timedelta
import json
import secrets
from pydantic import Field, StrictInt, StrictBool, ValidationError, model_validator

from . import enterprise_rest_feed as rest
from . import event_log_workspace as ws

FORMAT = "owg_enterprise_ingestion_service_levels_v1"
MAX_CLOSED_PER_PROFILE = 100
SIGNALS = ("success_age", "checkpoint_age", "consecutive_failures", "expired_intent", "integrity_hold")
RULES = """CREATE TABLE IF NOT EXISTS enterprise_ingestion_sla_rules (
 profile_id VARCHAR PRIMARY KEY, definition VARCHAR NOT NULL
)"""
EPISODES = """CREATE TABLE IF NOT EXISTS enterprise_ingestion_sla_episodes (
 episode_id VARCHAR PRIMARY KEY, profile_id VARCHAR NOT NULL, signal VARCHAR NOT NULL,
 active BOOLEAN NOT NULL, opened_at DOUBLE NOT NULL, definition VARCHAR NOT NULL
)"""


def init(c):
    c.execute(RULES)
    c.execute(EPISODES)


def exists(c):
    return bool(c.execute("""SELECT 1 FROM information_schema.tables
        WHERE table_name='enterprise_ingestion_sla_rules'""").fetchone())


def erase(c, pid):
    if exists(c):
        c.execute("DELETE FROM enterprise_ingestion_sla_episodes WHERE profile_id=?", [pid])
        c.execute("DELETE FROM enterprise_ingestion_sla_rules WHERE profile_id=?", [pid])


def require(ok, code, status=409):
    if not ok:
        raise rest.FeedError(code, status)


def parse(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_ingestion_service_level_request", 422) from None


class Save(rest.Strict):
    profile_id: str = Field(pattern=r"^rest_[a-f0-9]{24}$")
    profile_revision: StrictInt = Field(ge=1)
    expected_rule_revision: StrictInt = Field(ge=0)
    enabled: StrictBool
    interval_minutes: StrictInt = Field(ge=5, le=1440)
    success_age_hours: StrictInt | None = Field(default=None, ge=1, le=720)
    checkpoint_age_hours: StrictInt | None = Field(default=None, ge=1, le=8760)
    failure_streak: StrictInt | None = Field(default=None, ge=1, le=20)
    watch_expired_intent: StrictBool = True
    watch_integrity_hold: StrictBool = True
    confirm_local_monitoring: StrictBool

    @model_validator(mode="after")
    def selected(self):
        if not (self.success_age_hours or self.checkpoint_age_hours or self.failure_streak
                or self.watch_expired_intent or self.watch_integrity_hold):
            raise ValueError("select at least one signal")
        return self


class Ack(rest.Strict):
    episode_id: str = Field(pattern=r"^sla_[a-f0-9]{32}$")
    version: StrictInt = Field(ge=1)
    confirm_acknowledgement: StrictBool


def read_rule(c, pid):
    row = c.execute("SELECT definition FROM enterprise_ingestion_sla_rules WHERE profile_id=?", [pid]).fetchone()
    return json.loads(row[0]) if row else None


def put_rule(c, rule):
    c.execute("INSERT OR REPLACE INTO enterprise_ingestion_sla_rules VALUES (?,?)",
              [rule["profile_id"], json.dumps(rule, separators=(",", ":"), allow_nan=False)])


def put_episode(c, item):
    c.execute("INSERT OR REPLACE INTO enterprise_ingestion_sla_episodes VALUES (?,?,?,?,?,?)",
              [item["episode_id"], item["profile_id"], item["signal"], item["active"], item["opened_epoch"],
               json.dumps(item, separators=(",", ":"), allow_nan=False)])


def active(c, pid):
    rows = [json.loads(r[0]) for r in c.execute("""SELECT definition FROM enterprise_ingestion_sla_episodes
        WHERE profile_id=? AND active ORDER BY signal""", [pid]).fetchall()]
    require(len({x["signal"] for x in rows}) == len(rows), "duplicate_active_service_level_episode")
    return {x["signal"]: x for x in rows}


def prune(c, pid):
    c.execute("""DELETE FROM enterprise_ingestion_sla_episodes WHERE episode_id IN (
        SELECT episode_id FROM enterprise_ingestion_sla_episodes WHERE profile_id=? AND NOT active
        ORDER BY opened_at DESC,episode_id DESC OFFSET ?)""", [pid, MAX_CLOSED_PER_PROFILE])


def close(c, item, now, reason):
    item.update(active=False, closed_at=rest._iso(now), close_reason=reason,
                version=item["version"]+1)
    put_episode(c,item)


def save(value, *, now=None):
    spec = parse(Save,value)
    require(spec.confirm_local_monitoring, "explicit_local_monitoring_required", 403)
    now = now or rest._now()
    with ws._LOCK:
        c = rest._connect()
        try:
            c.execute("BEGIN")
            try:
                p = rest._get(c,spec.profile_id)
                require(p["revision"]==spec.profile_revision,"profile_changed")
                previous=read_rule(c,spec.profile_id)
                revision=previous["revision"] if previous else 0
                require(revision==spec.expected_rule_revision,"service_level_rule_changed")
                if previous and previous["last_evaluated_at"]:
                    require(now>=rest._stamp(previous["last_evaluated_at"]),"service_level_clock_regressed")
                # A changed policy never mislabels old episodes as recovered.
                for item in active(c,spec.profile_id).values():
                    close(c,item,now,"policy_replaced")
                rule=spec.model_dump(exclude={"expected_rule_revision","profile_revision","confirm_local_monitoring"})
                rule.update(revision=revision+1, provider=p.get("provider","rest"),
                            reviewed_profile_revision=p["revision"], saved_at=rest._iso(now),
                            last_evaluated_at=None,next_due_at=rest._iso(now) if spec.enabled else None,
                            last_observations=[])
                put_rule(c,rule)
                prune(c,spec.profile_id)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return rule
        finally:
            c.close()


def observation(signal, state, value=None, threshold=None, reason=None):
    return {"signal":signal,"state":state,"value":value,"threshold":threshold,"reason":reason}


def age_observation(signal, timestamp, threshold, now, *, first_seen):
    # Missing success/checkpoint becomes overdue only after the configured
    # grace from profile creation. A future clock value is unknown.
    try:
        stamp=rest._stamp(timestamp or first_seen)
    except (rest.FeedError,TypeError):
        return observation(signal,"unknown",threshold=threshold,reason="timestamp_unavailable")
    age=(now-stamp).total_seconds()
    if age<0:
        return observation(signal,"unknown",threshold=threshold,reason="timestamp_in_future")
    return observation(signal,"breached" if age>=threshold else "clear",int(age),threshold,
                       "since_first_configuration" if timestamp is None else "since_recorded_timestamp")


def failure_observation(c,p,threshold):
    # Count the current revision's newest admitted terminal attempts only.
    # An invalidated or interrupted run is not silently treated as a provider
    # failure or a success. Insufficient retained history stays unknown.
    rows=c.execute("""SELECT state FROM enterprise_ingestion_runs WHERE profile_id=? AND revision=?
        AND state<>'running' ORDER BY admitted_at DESC,run_id DESC LIMIT ?""",
        [p["profile_id"],p["revision"],threshold]).fetchall()
    count=0
    for (state,) in rows:
        if state=="failed":
            count+=1
        elif state=="succeeded":
            return observation("consecutive_failures","clear",count,threshold,"success_breaks_streak")
        else:
            return observation("consecutive_failures","unknown",count,threshold,"non_success_failure_terminal_intent")
    if count>=threshold:
        return observation("consecutive_failures","breached",count,threshold,"retained_terminal_attempts")
    return observation("consecutive_failures","unknown",count,threshold,"insufficient_terminal_history")


def observe(c,p,rule,now):
    selected=[]
    # Disabled feeds suspend age/streak signals; this is not recovery.
    suspended=not p["enabled"]
    for field,signal,stamp in (("success_age_hours","success_age",p["last_success_at"]),
                              ("checkpoint_age_hours","checkpoint_age",p["checkpoint"])):
        hours=rule[field]
        if hours is not None:
            threshold=hours*3600
            selected.append(observation(signal,"unknown",threshold=threshold,reason="feed_disabled")
                if suspended else age_observation(signal,stamp,threshold,now,first_seen=p["created_at"]))
    if rule["failure_streak"] is not None:
        selected.append(observation("consecutive_failures","unknown",threshold=rule["failure_streak"],reason="feed_disabled")
                        if suspended else failure_observation(c,p,rule["failure_streak"]))
    if rule["watch_expired_intent"]:
        expired=bool(p["run_id"]) and p["lease_until"]<=now.timestamp()
        selected.append(observation("expired_intent","breached" if expired else "clear",
                                    int(expired),1,"local_lease_only"))
    if rule["watch_integrity_hold"]:
        held=bool(p.get("integrity_hold"))
        selected.append(observation("integrity_hold","breached" if held else "clear",int(held),1,"local_hold_only"))
    return selected


def evaluate_one(c,p,rule,now):
    require(rule["enabled"],"service_level_monitor_disabled")
    if rule["last_evaluated_at"]:
        require(now>=rest._stamp(rule["last_evaluated_at"]),"service_level_clock_regressed")
    old=active(c,p["profile_id"])
    observations=observe(c,p,rule,now)
    opened=closed=0
    stamp=rest._iso(now)
    for obs in observations:
        signal=obs["signal"]
        item=old.get(signal)
        if obs["state"]=="breached":
            if item is None:
                item={"episode_id":"sla_"+secrets.token_hex(16),"profile_id":p["profile_id"],
                      "provider":p.get("provider","rest"),"signal":signal,"rule_revision":rule["revision"],
                      "opened_profile_revision":p["revision"],"opened_at":stamp,"opened_epoch":now.timestamp(),
                      "active":True,"version":1,"acknowledged_at":None,"closed_at":None,"close_reason":None}
                opened+=1
            item.update(last_observed_at=stamp,last_profile_revision=p["revision"],observation=obs)
            put_episode(c,item)
        elif item is not None:
            item.update(last_observed_at=stamp,last_profile_revision=p["revision"],observation=obs)
            if obs["state"]=="clear":
                close(c,item,now,"observed_clear")
                closed+=1
            else:
                put_episode(c,item)
    rule.update(last_evaluated_at=stamp,next_due_at=rest._iso(
        now+timedelta(minutes=rule["interval_minutes"])),last_observations=observations)
    put_rule(c,rule)
    prune(c,p["profile_id"])
    return {"profile_id":p["profile_id"],"opened":opened,"closed":closed,"observations":observations}


def evaluate_due(*, now=None, force=False):
    """Scheduler invokes due rules only; dashboard can explicitly refresh all enabled rules."""
    now=now or rest._now()
    # The scheduler calls this frequently. Before first opt-in, don't create
    # a workspace or monitor tables merely because the desktop is running.
    if not force and not ws.database_path().exists():
        return {"evaluated":[],"errors":[]}
    with ws._LOCK:
        c=rest._connect()
        try:
            rules=[json.loads(r[0]) for r in c.execute(
                "SELECT definition FROM enterprise_ingestion_sla_rules ORDER BY profile_id").fetchall()]
            results,errors=[],[]
            for rule in rules:
                if not rule["enabled"]:
                    continue
                try:
                    if not force and rest._stamp(rule["next_due_at"])>now:
                        continue
                    c.execute("BEGIN")
                    try:
                        p=rest._get(c,rule["profile_id"])
                        result=evaluate_one(c,p,rule,now)
                        c.execute("COMMIT")
                        results.append(result)
                    except Exception:
                        c.execute("ROLLBACK")
                        raise
                except Exception:
                    errors.append({"profile_id":rule["profile_id"],"error_code":"service_level_evaluation_failed"})
            return {"evaluated":results,"errors":errors,"provider_reads_performed":False,
                    "credential_refresh_performed":False,"provider_live_health_verified":False}
        finally:
            c.close()


def acknowledge(value,*,now=None):
    spec=parse(Ack,value)
    require(spec.confirm_acknowledgement,"explicit_alert_acknowledgement_required",403)
    now=now or rest._now()
    with ws._LOCK:
        c=rest._connect()
        try:
            c.execute("BEGIN")
            try:
                row=c.execute("SELECT definition FROM enterprise_ingestion_sla_episodes WHERE episode_id=?",
                              [spec.episode_id]).fetchone()
                require(row is not None,"unknown_service_level_episode",404)
                item=json.loads(row[0])
                require(item["active"] and item["version"]==spec.version,"service_level_episode_changed")
                require(now>=rest._stamp(item["opened_at"]),"service_level_clock_regressed")
                require(item["acknowledged_at"] is None,"service_level_episode_already_acknowledged")
                item.update(acknowledged_at=rest._iso(now),version=item["version"]+1)
                put_episode(c,item)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return item
        finally:
            c.close()


def inventory():
    with ws._LOCK:
        c=rest._connect()
        try:
            profiles=[]
            for (pid,) in c.execute("SELECT profile_id FROM enterprise_rest_profiles ORDER BY profile_id").fetchall():
                p=rest._get(c,pid)
                profiles.append({"profile_id":pid,"provider":p.get("provider","rest"),
                                 "revision":p["revision"],"enabled":p["enabled"],"rule":read_rule(c,pid)})
            episodes=[json.loads(r[0]) for r in c.execute("""SELECT definition FROM enterprise_ingestion_sla_episodes
                ORDER BY active DESC,opened_at DESC,episode_id DESC""").fetchall()]
            return {"format":FORMAT,"profiles":profiles,"episodes":episodes,
                    "max_closed_per_profile":MAX_CLOSED_PER_PROFILE,
                    "active_alerts":sum(x["active"] for x in episodes),
                    "unacknowledged_alerts":sum(x["active"] and not x["acknowledged_at"] for x in episodes),
                    "provider_reads_performed":False,"credential_refresh_performed":False,
                    "provider_live_health_verified":False,"remote_notifications_sent":False}
        finally:
            c.close()
