"""Content-free recorder status from the current enrolled identity's heartbeat.

No evidence timestamps, productivity inference, provider probes or remote controls.
"""
from datetime import datetime,timedelta,timezone


_STATES={'recording','paused','stopped','no_recent_collector_heartbeat'}


def _stamp(value):
    if not isinstance(value,str):raise ValueError('Missing status time')
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError('Missing status offset')
    return stamp.astimezone(timezone.utc)


def recorder_status(report,*,enrolled_at,identity_linked_at=None,now=None):
    """A fresh report is a device claim, not independent OS or business proof."""
    value={'format':'openworkgraph.fleet-recorder-status.v1','status':'unknown',
        'report_status':'no_report','reported_at':None,'report_age_seconds':None,'status_expires_at':None,
        'recorder_state':'unknown','fresh_authenticated_device_report':False,
        'collector_signal_recent':False,'evidence_age_used_for_status':False,
        'connector_health_checked':False,'evidence_sync_health_checked':False,
        'recording_is_proof_of_work':False}
    if not report:return value
    try:
        received=_stamp(report.get('received_at'));enrolled=_stamp(enrolled_at)
        if identity_linked_at is not None:enrolled=max(enrolled,_stamp(identity_linked_at))
        checked=now if now is not None else datetime.now(timezone.utc)
        if checked.tzinfo is None or checked.utcoffset() is None:raise ValueError('Missing current offset')
        age=(checked-received).total_seconds()
        if received<enrolled:
            value['report_status']='predates_enrollment';return value
        if age<0 or report.get('collector_state') not in _STATES:
            raise ValueError('Invalid status report')
        value.update(reported_at=received.isoformat(),report_age_seconds=int(age))
        if age>60:
            value['report_status']='stale_report';return value
        state=report['collector_state']
        value.update(report_status='current_report',recorder_state=state,
                     fresh_authenticated_device_report=True,status_expires_at=(received+timedelta(seconds=60)).isoformat())
        if state=='recording':
            collector_age=report.get('collector_age_seconds')
            recent=(report.get('capture_policy')=='recording' and type(collector_age) is int
                and 0<=collector_age and age+collector_age<=45)
            if recent:value['status_expires_at']=(received+timedelta(seconds=45-collector_age)).isoformat()
            value.update(collector_signal_recent=recent,
                         status='recording_reported' if recent else 'recording_signal_stale')
        else:
            value['status']={'paused':'paused_reported','stopped':'stopped_reported',
                             'no_recent_collector_heartbeat':'collector_unavailable'}[state]
        return value
    except (TypeError,ValueError,OverflowError):
        value['report_status']='invalid_report';return value


SYNC_STATES={'connected','syncing','error','not_started','local_only','stopped'}
SYNC_REPORT_SECONDS=120
BACKLOG_EVENTS=500
SYNC_SUCCESS_SECONDS=900


def sync_health(report,*,enrolled_at,identity_linked_at=None,now=None):
    """Content-free evidence-sync counts claimed by the device's current credential.

    The Gateway's own last-received evidence time stays a separate signal; these
    counts are device claims, not proof that every captured event was shared.
    """
    value={'format':'openworkgraph.fleet-sync-health.v1','status':'unknown',
        'report_status':'no_report','reported_at':None,'report_age_seconds':None,'status_expires_at':None,
        'sync_state':'unknown','unprocessed_local_events':None,'quarantined_events':None,
        'last_success_age_seconds':None,'sync_error_present':None,'problems':[],
        'fresh_authenticated_device_report':False,'evidence_content_received':False,
        'all_captured_evidence_shared_verified':False}
    if not report:return value
    try:
        received=_stamp(report.get('received_at'));enrolled=_stamp(enrolled_at)
        if identity_linked_at is not None:enrolled=max(enrolled,_stamp(identity_linked_at))
        checked=now if now is not None else datetime.now(timezone.utc)
        if checked.tzinfo is None or checked.utcoffset() is None:raise ValueError('Missing current offset')
        age=(checked-received).total_seconds()
        if received<enrolled:
            value['report_status']='predates_enrollment';return value
        state=report.get('sync_state')
        counts=[report.get(key) for key in ('unprocessed_local_events','quarantined_events')]
        success=report.get('last_success_age_seconds')
        error=report.get('sync_error_present')
        if (age<0 or state not in SYNC_STATES or any(type(n) is not int or n<0 for n in counts)
                or (success is not None and (type(success) is not int or success<0))
                or error not in (0,1,True,False)):
            raise ValueError('Invalid sync report')
        value.update(reported_at=received.isoformat(),report_age_seconds=int(age))
        if age>SYNC_REPORT_SECONDS:
            value['report_status']='stale_report';return value
        pending,quarantined=counts;error=bool(error)
        # Elapsed time since receipt also ages the device's own last-success claim.
        since_success=None if success is None else success+int(age)
        problems=[]
        if error or state=='error':problems.append('sync_error_reported')
        if quarantined:problems.append('quarantined_events_reported')
        if pending>=BACKLOG_EVENTS or (pending and (since_success is None or since_success>SYNC_SUCCESS_SECONDS)):
            problems.append('backlog_reported')
        if state in {'not_started','local_only','stopped'}:problems.append('sync_not_running_reported')
        value.update(report_status='current_report',sync_state=state,unprocessed_local_events=pending,
            quarantined_events=quarantined,last_success_age_seconds=since_success,sync_error_present=error,
            problems=problems,fresh_authenticated_device_report=True,
            status_expires_at=(received+timedelta(seconds=SYNC_REPORT_SECONDS)).isoformat(),
            status=problems[0] if problems else 'in_sync_reported')
        return value
    except (TypeError,ValueError,OverflowError):
        value['report_status']='invalid_report';return value
