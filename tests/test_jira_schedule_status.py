"""Schedule configuration is not Jira provider health or source completeness."""

import pytest

from server import event_log_workspace as workspace
from server import jira_retained_profiles as profiles
from server import local_scheduler as scheduler


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path / 'logs')
    monkeypatch.setattr(scheduler, 'DATA_DIR', tmp_path / 'local')
    monkeypatch.setattr(profiles, 'configuration', lambda env: ('owned.atlassian.net', 'cloud', 'token'))
    with profiles._connect() as conn:
        for n in (1, 2):
            profiles._put(conn, {
                'profile_id': 'jira_' + str(n) * 24,
                'resource_refs': ['owg:r:' + str(n) * 32], 'issue_keys': ['OWG-' + str(n)],
                'enabled': True, 'status': 'ready', 'revision': 1,
                'credential_check_status': 'unverified',
            })
    return 'jira_' + '1' * 24, 'jira_' + '2' * 24


def test_schedule_create_pause_resume_delete_project_accurate_flags(scoped):
    first, second = scoped
    status = profiles.status()
    assert status['manual_runs_only'] is True and status['scheduled_runs_enabled'] is False
    assert all(p['manual_runs_only'] and not p['scheduled_runs_enabled'] for p in status['profiles'])
    schedule = scheduler.create('retained_jira_profile', first,
        interval_minutes=15, confirm_scheduled_runs=True)
    status = profiles.status()
    assert status['manual_runs_only'] is False and status['scheduled_runs_enabled'] is True
    a, b = status['profiles']
    assert a['scheduled_runs_enabled'] is True and a['manual_runs_only'] is False
    assert b['profile_id'] == second and b['manual_runs_only'] is True
    assert a['source_completeness_verified'] is False and a['business_outcomes_verified'] is False
    assert a['credential_check_status'] == 'unverified' and a['alert_eligible'] is False
    scheduler.update(schedule['schedule_id'], enabled=False)
    assert profiles.status()['manual_runs_only'] is True
    scheduler.update(schedule['schedule_id'], enabled=True)
    assert profiles.status()['scheduled_runs_enabled'] is True
    scheduler.delete(schedule['schedule_id'])
    assert profiles.status()['manual_runs_only'] is True


def test_unrelated_job_or_deleted_profile_cannot_claim_scheduled_jira(scoped, monkeypatch):
    first, second = scoped
    monkeypatch.setattr(scheduler, '_check_target', lambda kind, target: None)
    monkeypatch.setattr(scheduler, "_signal_rule_set_pin", lambda log_id: {"fingerprint": "a" * 64, "count": 1})
    scheduler.create('event_log_signals', first, interval_minutes=15, confirm_scheduled_runs=True)
    assert profiles.status()['scheduled_runs_enabled'] is False
    scheduler.create('retained_jira_profile', first, interval_minutes=15, confirm_scheduled_runs=True)
    with profiles._connect() as conn:
        conn.execute('DELETE FROM jira_ingestion_profiles WHERE profile_id=?', [first])
    status = profiles.status()
    assert status['manual_runs_only'] is True and status['scheduled_runs_enabled'] is False
    assert [p['profile_id'] for p in status['profiles']] == [second]


def test_status_does_not_execute_jobs_or_claim_credential_health(scoped, monkeypatch):
    first, _ = scoped
    schedule = scheduler.create('retained_jira_profile', first,
        interval_minutes=15, confirm_scheduled_runs=True,
        quiet_hours={'timezone': 'UTC', 'start': '01:00', 'end': '23:00'})
    def forbidden(*args, **kwargs):
        pytest.fail('status must not execute jobs or make provider reads')
    monkeypatch.setattr(scheduler, '_run_job', forbidden)
    monkeypatch.setattr(profiles, 'Extractor', forbidden)
    before = scheduler._read()
    status = profiles.status()
    assert status['scheduled_runs_enabled'] is True
    assert status['profiles'][0]['credential_check_status'] == 'unverified'
    assert scheduler._read() == before
    assert scheduler._read()['schedules'][schedule['schedule_id']]['history'] == []
