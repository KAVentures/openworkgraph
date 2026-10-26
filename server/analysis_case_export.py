"""Prepare complete minimized case data and its aggregate under one local lock."""
from datetime import datetime, timezone

from shared.analysis_case_dataset import CaseDataset, MAX_CASES, MAX_EVENTS
from . import event_log_cohorts as cohorts, event_log_query as analysis, event_log_workspace as workspace
from .analysis_snapshot_export import export_snapshot


def prepare(log_id, spec):
    parsed = analysis.parse(spec)
    if parsed.level != 'cases' or parsed.activity_in or any(v is not None for v in parsed.where.model_dump().values()):
        raise workspace.EventLogWorkspaceError('Case sharing currently supports case-level analyses with cohort filters only; clear analysis-specific case/activity filters')

    def selected(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(parsed.cohort), log_id)
        count, events = conn.execute('SELECT count(DISTINCT case_id), count(*) FROM ce').fetchone()
        if not 0 < count <= MAX_CASES or events > MAX_EVENTS:
            raise workspace.EventLogWorkspaceError('Select a nonempty complete cohort of at most 500 cases and 5000 events; no partial dataset was prepared')
        # Select only required fields; private attributes/resources never enter the packet.
        return conn.execute('SELECT case_id, activity, ts FROM ce ORDER BY case_id, ts, event_id').fetchall()

    with workspace._LOCK:
        aggregate = export_snapshot(log_id, parsed.model_dump())
        rows = workspace._query(log_id, parsed.case_type, lambda c: {'rows':selected(c)})['rows']
        cases, activities = [], {}
        previous = None
        for case_id, activity, at in rows:
            if case_id != previous:
                previous = case_id; started = at
                cases.append({'code': f'C{len(cases)+1}', 'events': []})
            code = activities.setdefault(activity, f'A{len(activities)+1}')
            elapsed = at - started
            us = (elapsed.days * 86400 + elapsed.seconds) * 1000000 + elapsed.microseconds
            cases[-1]['events'].append({'activity': code, 'elapsed_microseconds': us})
        if len(cases) != aggregate['matching_cases']:
            raise workspace.EventLogWorkspaceError('Case population differs from the aggregate; no dataset was prepared')
        try:
            packet = CaseDataset(generated_at=datetime.now(timezone.utc),cases=cases,
            source_content_sha256=aggregate['source_content_sha256'],analysis_spec_sha256=aggregate['analysis_spec_sha256'])
        except ValueError:
            raise workspace.EventLogWorkspaceError('Case dataset exceeds its code/time/size bounds; choose a narrower cohort') from None
        return {'aggregate_snapshot':aggregate,'case_dataset':packet.model_dump(mode='json')}
