"""Sales-lead pack on a synthetic selected CRM export."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace

PACK = 'sales_lead_management'
MAPPING = {
    'object_types': {'lead': 'case'},
    'activities': {'create_lead': 'Lead created', 'qualify_lead': 'Qualified',
                   'create_opportunity': 'Opportunity created', 'review_lead': 'Reviewed',
                   'reopen_lead': 'Reopened'},
}
CSV = '''lead,activity,time
L1,Lead created,2026-01-01T00:00:00Z
L1,Reviewed,2026-01-01T00:30:00Z
L1,Qualified,2026-01-01T01:00:00Z
L1,Opportunity created,2026-01-01T03:00:00Z
L2,Lead created,2026-01-01T00:00:00Z
L2,Qualified,2026-01-04T00:00:00Z
L2,Opportunity created,2026-01-06T00:00:00Z
L3,Lead created,2026-01-01T00:00:00Z
L3,Reopened,2026-01-01T01:00:00Z
L3,Qualified,2026-01-01T02:00:00Z
'''


@pytest.fixture(autouse=True)
def local_data(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def imported():
    return workspace.import_log(BytesIO(CSV.encode()), name='Synthetic leads', source_format='csv',
                                mapping={'case_column':'lead', 'activity_column':'activity', 'timestamp_column':'time'})['log_id']


def test_sales_lead_pack_is_discoverable_and_works_on_selected_csv():
    pack = next(p for p in packs.catalog()['packs'] if p['pack_id'] == PACK)
    assert pack['latest_version'] == '1.0.0'
    log = imported()
    installed = packs.install(log, PACK, {'version':'1.0.0', 'mapping':MAPPING})
    assert installed['not_applicable'] == [] and len(installed['metric_ids']) == 4
    report = packs.report(log, PACK)
    assert report['source_system_validated'] is False
    metrics = {m['name']:m for m in report['metrics']}
    assert metrics['sales_lead_time_to_qualify']['median'] == 2 * 3600
    assert metrics['sales_lead_qualification_to_opportunity']['coverage']['cases_without_to_activity'] == 1
    assert metrics['sales_lead_reopens']['mean'] == pytest.approx(1 / 3)
    rules = [r for group in report['compliance'] for r in group['results']]
    qualify = next(r for r in rules if r['rule']['template'] == 'response_within' and r['rule']['a'] == 'Lead created')
    assert qualify['rule']['seconds'] == 48 * 3600 and qualify['violating_cases'] == 1
    handoff = next(r for r in rules if r['rule']['template'] == 'response_within' and r['rule']['a'] == 'Qualified')
    assert handoff['violating_cases'] == 2  # late and not observed, not verified business breaches
    assert any('Open, rejected' in assumption for assumption in report['assumptions'])


def test_optional_mappings_and_user_targets_are_respected():
    log = imported()
    mapping = {'object_types': MAPPING['object_types'],
               'activities': {k:v for k,v in MAPPING['activities'].items() if k not in {'review_lead', 'reopen_lead'}}}
    installed = packs.install(log, PACK, {'version':'1.0.0', 'mapping':mapping,
                                          'parameters':{'qualification_hours':100, 'handoff_hours':100}})
    assert len(installed['metric_ids']) == 2
    assert {x['unmapped_role'] for x in installed['not_applicable']} == {'review_lead', 'reopen_lead'}
    rules = [r for group in packs.report(log, PACK)['compliance'] for r in group['results']]
    qualify = next(r for r in rules if r['rule']['template'] == 'response_within' and r['rule']['a'] == 'Lead created')
    assert qualify['violating_cases'] == 0
    assert packs.uninstall(log, PACK)['metrics_removed'] == 2


def test_duplicate_opportunity_events_are_counted_per_lead_case():
    # L1 has two observed creation events, L2 one, and L3 none; the rule
    # counts events per lead, not distinct CRM opportunities or links per event.
    content = CSV + 'L1,Opportunity created,2026-01-01T04:00:00Z\n'
    log = workspace.import_log(BytesIO(content.encode()), name='Synthetic leads', source_format='csv',
                               mapping={'case_column':'lead', 'activity_column':'activity', 'timestamp_column':'time'})['log_id']
    packs.install(log, PACK, {'version':'1.0.0', 'mapping':MAPPING})
    report = packs.report(log, PACK)
    rules = [r for group in report['compliance'] for r in group['results']]
    count = next(r for r in rules if r['rule']['template'] == 'existence' and r['rule']['a'] == 'Opportunity created')
    assert count['rule']['min'] == 0 and count['rule']['max'] == 1
    assert count['cases'] == 3 and count['violating_cases'] == 1
    assert count['violating_case_examples'] == [{'case_id': 'L1', 'detail': '2 occurrences'}]
    assert not report['source_system_validated']
