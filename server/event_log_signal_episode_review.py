"""Retained evaluated signal episodes, not verified business incidents.

An episode is a retained trigger followed by an unambiguous subsequent
clearance of the same saved rule within the explicitly selected UTC window.
Incomplete or inconsistent boundaries are never silently repaired.
"""
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import groupby
import json
import re
from statistics import mean, median

from . import event_log_workspace as workspace
from . import event_log_signal_timeline as timeline

FORMAT="openworkgraph.signal-episode-review.v1"
MAX_TRANSITIONS=100_000
MAX_PAGE=100
STATUSES=("cleared","unresolved_at_window_end","interrupted_by_repeated_trigger",
          "interrupted_by_ambiguous_timestamp","clearance_without_retained_trigger",
          "ambiguous_timestamp")


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value):
        raise workspace.EventLogWorkspaceError("Use explicit UTC dates YYYY-MM-DD")
    try:return datetime.fromisoformat(value)
    except ValueError:raise workspace.EventLogWorkspaceError("Invalid UTC date") from None


def _stamp(value):
    return timeline._utc(value) if value is not None else None


def reconstruct(rows):
    """Rows sorted by (log, rule, timestamp, transition id).

    Same-timestamp transitions cannot establish chronology. Break any
    pending episode at such a group rather than selecting by random ID.
    Every transition appears exactly once among returned evidence rows.
    """
    result=[]
    for (log,rule),records in groupby(rows,key=lambda r:(r[0],r[2])):
        pending=None
        def emit(status,trigger=None,clear=None,ambiguous=()):
            first=trigger or clear or ambiguous[0]
            elapsed=(clear[4]-trigger[4]).total_seconds() if trigger and clear else None
            result.append({"log_id":log,"log_name":first[1],"rule_id":rule,
                "rule_name":timeline._name(first[3]),"status":status,
                "trigger_event_id":trigger[5] if trigger else None,
                "trigger_at":_stamp(trigger[4]) if trigger else None,
                "clear_event_id":clear[5] if clear else None,
                "clear_at":_stamp(clear[4]) if clear else None,
                "ambiguous_at":_stamp(ambiguous[0][4]) if ambiguous else None,
                "ambiguous_transition_ids":[r[5] for r in ambiguous],
                "ambiguous_trigger_count":sum(r[6]=="triggered" for r in ambiguous),
                "ambiguous_clear_count":sum(r[6]=="cleared" for r in ambiguous),
                "elapsed_seconds":elapsed,
                "sort_at":first[4],"sort_id":first[5]})
        for _,items in groupby(records,key=lambda r:r[4]):
            batch=list(items)
            if any(r[6] not in timeline.FILTERS-{"all"} for r in batch):
                raise workspace.EventLogWorkspaceError("Unsupported historical signal transition")
            if len(batch)>1:
                if pending:
                    emit("interrupted_by_ambiguous_timestamp",trigger=pending);pending=None
                emit("ambiguous_timestamp",ambiguous=batch)
                continue
            row=batch[0]
            if row[6]=="triggered":
                if pending:emit("interrupted_by_repeated_trigger",trigger=pending)
                pending=row
            elif pending:
                emit("cleared",trigger=pending,clear=row);pending=None
            else:emit("clearance_without_retained_trigger",clear=row)
        if pending:emit("unresolved_at_window_end",trigger=pending)
    return sorted(result,key=lambda r:(r["sort_at"],r["log_id"],r["rule_id"],r["sort_id"]),reverse=True)


def review(*,start_date,end_date,status="all",offset=0,limit=50,expected_review_sha256=None,
           log_id=None,rule_id=None):
    start,end=_date(start_date),_date(end_date)
    if end<=start or end-start>timedelta(days=366):
        raise workspace.EventLogWorkspaceError("Select a positive UTC window of at most 366 days")
    for value in (log_id,rule_id):
        if value is not None and (not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",value)):
            raise workspace.EventLogWorkspaceError("Invalid exact signal log or rule identifier")
    if rule_id is not None and log_id is None:
        raise workspace.EventLogWorkspaceError("Select a log before filtering its rule")
    if status not in ("all",*STATUSES):
        raise workspace.EventLogWorkspaceError("Unknown episode status")
    if type(offset) is not int or not 0<=offset<=MAX_TRANSITIONS or type(limit) is not int or not 1<=limit<=MAX_PAGE:
        raise workspace.EventLogWorkspaceError("Invalid episode evidence page")
    if expected_review_sha256 is not None and (not isinstance(expected_review_sha256,str)
            or not re.fullmatch(r"[a-f0-9]{64}",expected_review_sha256)):
        raise workspace.EventLogWorkspaceError("Invalid episode review digest")
    if offset and not expected_review_sha256:
        raise workspace.EventLogWorkspaceError("Review the first episode page before paging")
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            base="""FROM signal_transitions t JOIN logs l ON l.log_id=t.log_id
                JOIN signal_rules s ON s.log_id=t.log_id AND s.rule_id=t.rule_id
                WHERE t.occurred_at>=? AND t.occurred_at<?"""
            count=conn.execute("SELECT count(*) "+base,[start,end]).fetchone()[0]
            if count>MAX_TRANSITIONS:
                raise workspace.EventLogWorkspaceError("Signal episode review exceeds the complete transition budget; select a shorter window")
            rows=conn.execute("""SELECT t.log_id,l.name,t.rule_id,s.definition,t.occurred_at,t.transition_id,t.event_type
                """+base+" ORDER BY t.log_id,t.rule_id,t.occurred_at,t.transition_id",[start,end]).fetchall()
        finally:conn.close()
    digest=sha256()
    def feed(value):
        digest.update(json.dumps(value,ensure_ascii=True,separators=(",",":"),sort_keys=True).encode()+b"\n")
    feed([FORMAT,start_date,end_date])
    for r in rows:feed([*r[:4],_stamp(r[4]),*r[5:]])
    pin=digest.hexdigest()
    if expected_review_sha256 and expected_review_sha256!=pin:
        raise workspace.EventLogWorkspaceError("Retained episode evidence changed; refresh the review")
    episodes=reconstruct(rows)
    counts=dict.fromkeys(STATUSES,0);counts.update(Counter(r["status"] for r in episodes))
    durations=[r["elapsed_seconds"] for r in episodes if r["status"]=="cleared"]
    trigger_count=sum(r[6]=="triggered" for r in rows)
    clear_count=len(rows)-trigger_count
    # These are counts of journal rows, not estimates of actual incident
    # frequency. Ambiguous batches can contain many rows.
    accounted=sum(bool(r["trigger_event_id"])+bool(r["clear_event_id"])+len(r["ambiguous_transition_ids"])
                  for r in episodes)
    if accounted!=len(rows):
        raise workspace.EventLogWorkspaceError("Episode transition accounting failed")
    selected=[r for r in episodes if (status=="all" or r["status"]==status)
              and (log_id is None or r["log_id"]==log_id)
              and (rule_id is None or r["rule_id"]==rule_id)]
    page=selected[offset:offset+limit]
    return {"format":FORMAT,"review_sha256":pin,"start_date_utc":start_date,"end_date_utc_exclusive":end_date,
            "retained_transition_count":len(rows),"retained_trigger_count":trigger_count,
            "retained_clear_count":clear_count,"accounted_transitions":accounted,
            "retained_rule_count":len({(r[0],r[2]) for r in rows}),
            "retained_log_count":len({r[0] for r in rows}),
            "episode_row_count":len(episodes),"status_counts":counts,
            "observed_trigger_to_clear_seconds":{
                "count":len(durations),"minimum":min(durations) if durations else None,
                "p50":median(durations) if durations else None,
                "maximum":max(durations) if durations else None,
                "mean":mean(durations) if durations else None},
            "page":{"status":status,"log_id":log_id,"rule_id":rule_id,"matching_rows":len(selected),"offset":offset,
                    "next_offset":offset+len(page) if page and offset+len(page)<len(selected) else None,
                    "rows":[{k:v for k,v in r.items() if k not in ("sort_at","sort_id")} for r in page]},
            "window_is_complete_for_retained_selected_transitions":True,
            "historical_journal_completeness_verified":False,"actual_incident_duration_verified":False,
            "current_state_check":False,"source_data_read":False,"evaluations_performed":False,
            "notifications_sent":False,"actions_authorized":False,
            "interpretation":[
                "The UTC window is start-inclusive and end-exclusive. Only retained transitions for existing logs and rules are considered. Earlier triggers, later clearances, trimmed or deleted history and intervals before journal creation are not reconstructed.",
                "An observed duration is the time between two recorded evaluations, not actual outage, work, response or recovery time. Rule names are current retained names, not historical names.",
                "Repeated triggers and simultaneous timestamp groups interrupt pending episodes. Leading clearances and unresolved triggers remain separate evidence; random transition IDs never establish chronology.",
                "No live source check, notification, evaluation, acknowledgement or business action occurs."]}
