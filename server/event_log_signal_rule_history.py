"""Read-only, complete-window rule summaries with exact evidence pinning."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from shared import signal_rule_history as core
from . import event_log_signal_episode_review as episodes
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.signal-rule-history.v1"
SORTS = ("deadline", "recurrence", "volume")
INTERPRETATION = [
    "Only retained evaluated transitions belonging to existing logs and rules are considered. No historical completeness, actual outage duration or recovery is verified.",
    "Cleared pairs use recorded evaluation timestamps. Unresolved triggers with a window extending strictly past the threshold are shown separately; they are not observed late clearances. Interrupted and ambiguous triggers are never imputed.",
    "Retrigger gaps use a completed pair followed by the next adjacent unambiguous trigger for the same exact rule. Leading clearances, timestamp ties and intervening evidence interrupt the sequence. A retrigger does not establish a recurring business incident.",
    "The requested ordering is descriptive, not risk severity or employee performance. Rules with no retained changes in the window are absent, not healthy.",
]


def review(*, start_date, end_date, deadline_seconds, recurrence_seconds, sort="deadline",
           log_id=None, offset=0, limit=30, expected_review_sha256=None):
    end = episodes._date(end_date)
    if end > datetime.now(timezone.utc).replace(tzinfo=None):
        raise workspace.EventLogWorkspaceError("Select a completed UTC window; the exclusive end cannot be in the future")
    if any(type(x) is not int or not 1 <= x <= 366*86400 for x in (deadline_seconds, recurrence_seconds)):
        raise workspace.EventLogWorkspaceError("Choose positive whole-second thresholds of at most 366 days")
    if sort not in SORTS:
        raise workspace.EventLogWorkspaceError("Unknown rule review ordering")
    if log_id is not None and (not isinstance(log_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",log_id)):
        raise workspace.EventLogWorkspaceError("Invalid exact log identifier")
    if type(offset) is not int or not 0 <= offset <= episodes.MAX_TRANSITIONS or type(limit) is not int or not 1 <= limit <= 100:
        raise workspace.EventLogWorkspaceError("Invalid rule history page")
    if expected_review_sha256 is not None and (not isinstance(expected_review_sha256,str)
            or not re.fullmatch(r"[a-f0-9]{64}",expected_review_sha256)):
        raise workspace.EventLogWorkspaceError("Invalid rule history digest")
    if offset and not expected_review_sha256:
        raise workspace.EventLogWorkspaceError("Review page one before paging")
    raw, reconstructed, source_pin = episodes.population(start_date,end_date)
    pin = sha256(json.dumps([FORMAT, source_pin, deadline_seconds, recurrence_seconds, sort, log_id],
                            ensure_ascii=True,separators=(",",":")).encode()).hexdigest()
    if expected_review_sha256 and pin != expected_review_sha256:
        raise workspace.EventLogWorkspaceError("Retained rule history or reviewed settings changed; refresh the review")
    try:
        all_rules = core.summarize(reconstructed,end,deadline_seconds,recurrence_seconds)
    except ValueError as exc:
        raise workspace.EventLogWorkspaceError(str(exc)) from None
    all_totals = core.totals(all_rules)
    if (all_totals["retained_triggers"] != sum(r[6]=="triggered" for r in raw)
            or all_totals["retained_clearances"] != sum(r[6]=="cleared" for r in raw)):
        raise workspace.EventLogWorkspaceError("Retained rule history accounting failed")
    matching = core.ordered([r for r in all_rules if log_id is None or r["log_id"]==log_id],sort)
    page = matching[offset:offset+limit]
    return {"format":FORMAT,"review_sha256":pin,"source_episode_review_sha256":source_pin,
            "start_date_utc":start_date,"end_date_utc_exclusive":end_date,
            "deadline_seconds":deadline_seconds,"recurrence_seconds":recurrence_seconds,
            "sort":sort,"log_id":log_id,"all_rule_count":len(all_rules),"all_totals":all_totals,
            "matching_rule_count":len(matching),"matching_totals":core.totals(matching),
            "offset":offset,"next_offset":offset+len(page) if page and offset+len(page)<len(matching) else None,
            "rows":page,"window_is_complete_for_retained_selected_transitions":True,
            "historical_journal_completeness_verified":False,"actual_incident_duration_verified":False,
            "current_state_check":False,"source_data_read":False,"evaluations_performed":False,
            "notifications_sent":False,"actions_authorized":False,"interpretation":INTERPRETATION}
