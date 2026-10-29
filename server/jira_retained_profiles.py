"""Local Jira ingestion profiles, lifecycle and atomic supplied-ID checkpoints.

All durable state shares the existing event-log DuckDB and workspace lock.
Network reads never hold that lock. No scheduler, inventory scan or MCP route.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
from io import BytesIO, StringIO
import json
import os
import re
import secrets
import time
import tempfile
from pathlib import Path

from . import event_log_workspace as workspace
from . import jira_ingestion_staging as staging
from .jira_retained_source import configuration, digest, Extractor, JiraIngestionError
from .jira_source_evidence import _ISSUE, _REF
from .local_auth import ensure_api_token
from .local_reference_lookup import resolve_resource_reference

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS jira_ingestion_profiles (
  profile_id VARCHAR PRIMARY KEY, definition VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS jira_ingestion_manifest (
  profile_id VARCHAR NOT NULL, event_key VARCHAR NOT NULL, payload_sha256 VARCHAR NOT NULL,
  PRIMARY KEY(profile_id, event_key)
);
'''
_PROFILE = re.compile(r'^jira_[0-9a-f]{24}$')
_LEASE_SECONDS = 120


def _connect():
    conn = workspace._connect()
    conn.execute(_SCHEMA)
    return conn


def _get(conn, profile_id):
    if not isinstance(profile_id, str) or not _PROFILE.fullmatch(profile_id):
        raise JiraIngestionError('unknown_jira_profile')
    row = conn.execute('SELECT definition FROM jira_ingestion_profiles WHERE profile_id=?', [profile_id]).fetchone()
    if row is None:
        raise JiraIngestionError('unknown_jira_profile')
    return json.loads(row[0])


def _put(conn, profile):
    conn.execute('INSERT OR REPLACE INTO jira_ingestion_profiles VALUES (?, ?)',
                 [profile['profile_id'], json.dumps(profile, separators=(',', ':'))])


def _binding(config):
    # A reference to this exact credential/configuration, never the credential.
    return hmac.new(ensure_api_token().encode(), json.dumps(config).encode(), hashlib.sha256).hexdigest()


def _scope(refs, host):
    if (not isinstance(refs, list) or not 1 <= len(refs) <= 8
            or any(not isinstance(ref, str) or not _REF.fullmatch(ref) for ref in refs)
            or len(set(refs)) != len(refs)):
        raise JiraIngestionError('select_one_to_eight_distinct_observed_jira_refs')
    keys = []
    for ref in refs:
        value = resolve_resource_reference(ref)
        if (not isinstance(value, dict)
                or (value.get('provider'), value.get('resource_kind'), value.get('host')) != ('jira', 'issue', host)
                or not isinstance(value.get('resolver_locator'), str) or not _ISSUE.fullmatch(value['resolver_locator'])):
            raise JiraIngestionError('observed_jira_scope_unavailable')
        keys.append(value['resolver_locator'])
    if len(set(keys)) != len(keys):
        raise JiraIngestionError('selected_issue_alias_collision')
    return keys


def native_selection_enabled():
    return os.environ.get('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED') == 'true'


def _native_scope(keys):
    if not native_selection_enabled():
        raise JiraIngestionError('native_jira_case_selection_disabled')
    if (not isinstance(keys, list) or not 1 <= len(keys) <= 8
            or any(not isinstance(key, str) or not _ISSUE.fullmatch(key) for key in keys)
            or len(set(keys)) != len(keys)):
        raise JiraIngestionError('select_one_to_eight_distinct_exact_jira_keys')
    return list(keys)


def _profile_scope(profile, host):
    basis = profile.get('selection_basis', 'observed_refs')
    if basis == 'observed_refs':
        return _scope(profile['resource_refs'], host)
    if basis == 'native_issue_keys' and profile['resource_refs'] == []:
        return _native_scope(profile['issue_keys'])
    raise JiraIngestionError('invalid_jira_profile_selection_basis')


def _public(profile, now):
    allowed = ('profile_id', 'host', 'resource_refs', 'enabled', 'revision', 'log_id',
               'status', 'error', 'last_attempt_at', 'last_success_at', 'retry_after', 'checkpoint',
               'credential_generation', 'credential_check_status', 'credential_check_error',
               'credential_check_attempt_at', 'credential_verified_at')
    result = {k: profile[k] for k in allowed if k in profile}
    if result['status'] in {'running', 'checking_credentials'} and profile.get('lease_until', 0) <= now:
        result['status'] = 'interrupted'
        if profile.get('credential_check_status') == 'checking':
            result['credential_check_status'] = 'interrupted'
    basis = profile.get('selection_basis', 'observed_refs')
    result.update(selection_basis=basis, selected_case_count=len(profile['issue_keys']))
    if basis == 'native_issue_keys':
        result['selected_issue_keys'] = list(profile['issue_keys'])
    result.update(provider='jira', manual_runs_only=True, source_completeness_verified=False,
                  business_outcomes_verified=False, execution_authorized=False,
                  alert_eligible=False, credential_values_included=False)
    return result


def status():
    try:
        configuration(os.environ)
        configured = True
    except JiraIngestionError:
        configured = False
    with workspace._LOCK, _connect() as conn:
        now = time.time()
        profiles = [_public(json.loads(row[0]), now) for row in conn.execute(
            'SELECT definition FROM jira_ingestion_profiles ORDER BY profile_id').fetchall()]
    return {'format': 'openworkgraph.retained-jira-profiles.v1', 'configured': configured,
            'profiles': profiles, 'manual_runs_only': True, 'credentials_exposed': False,
            'native_case_selection_configured': configured and native_selection_enabled()}


def _create(refs, *, issue_keys=None, confirm_reads, confirm_retention, confirm_status_labels):
    if not all(v is True for v in (confirm_reads, confirm_retention, confirm_status_labels)):
        raise JiraIngestionError('separate_jira_read_retention_and_label_confirmation_required')
    config = configuration(os.environ)
    basis = 'observed_refs' if issue_keys is None else 'native_issue_keys'
    keys = _scope(refs, config[0]) if basis == 'observed_refs' else _native_scope(issue_keys)
    profile_id = 'jira_' + secrets.token_hex(12)
    profile = {'profile_id': profile_id, 'host': config[0], 'cloud_id': config[1],
               'credential_reference': 'environment:OWG_JIRA_OAUTH_ACCESS_TOKEN',
               'configuration_binding': _binding(config), 'resource_refs': list(refs or []), 'issue_keys': keys,
               'selection_basis': basis,
               'enabled': True, 'revision': 1, 'log_id': None, 'account_binding': None,
               'status': 'ready', 'error': None, 'last_attempt_at': None,
               'last_success_at': None, 'retry_after': 0, 'checkpoint': None}
    with workspace._LOCK, _connect() as conn:
        if conn.execute('SELECT count(*) FROM jira_ingestion_profiles').fetchone()[0] >= 10:
            raise JiraIngestionError('jira_profile_limit_reached')
        _put(conn, profile)
    return _public(profile, time.time())


def create(refs, *, confirm_reads, confirm_retention, confirm_status_labels):
    return _create(refs, confirm_reads=confirm_reads, confirm_retention=confirm_retention,
                   confirm_status_labels=confirm_status_labels)


def create_native(issue_keys, *, confirm_reads, confirm_retention, confirm_status_labels):
    # None must not fall back to the observed-ref mode through _create's default.
    keys = _native_scope(issue_keys)
    return _create([], issue_keys=keys, confirm_reads=confirm_reads,
                   confirm_retention=confirm_retention, confirm_status_labels=confirm_status_labels)


def _interrupt_credential_check(profile):
    if profile.get('credential_check_status') == 'checking':
        profile.update(credential_check_status='interrupted',
                       credential_check_error='jira_credential_check_invalidated')
    profile.pop('credential_check_previous_status', None)
    profile.pop('credential_check_previous_error', None)


def change(profile_id, *, enabled=None, disconnect=False, confirmed=False):
    if not disconnect and (type(enabled) is not bool or (enabled and confirmed is not True)):
        raise JiraIngestionError('explicit_jira_enable_confirmation_required')
    with workspace._LOCK, _connect() as conn:
        profile = _get(conn, profile_id)
        if not disconnect and profile['status'] == 'dataset_deleted':
            raise JiraIngestionError('retained_jira_dataset_deleted')
        if disconnect:
            staging.clear(workspace.DATA_DIR, profile_id)
            conn.execute('BEGIN')
            try:
                conn.execute('DELETE FROM jira_ingestion_manifest WHERE profile_id=?', [profile_id])
                conn.execute('DELETE FROM jira_ingestion_profiles WHERE profile_id=?', [profile_id])
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK')
                raise
            return {'disconnected': True, 'retained_log_id': profile['log_id'], 'retained_dataset_deleted': False}
        _interrupt_credential_check(profile)
        profile.update(enabled=enabled, revision=profile['revision'] + 1,
                       run_id=None, lease_until=0, status='ready' if enabled else 'disabled', error=None)
        _put(conn, profile)
        return _public(profile, time.time())


def forget_dataset(conn, log_id):
    """Erase source-event manifest/checkpoint with the dataset, in its transaction.

    Configuration/selected refs remain until profile disconnect; they are not
    retained status events. Never initialize profile tables for legacy deletes.
    """
    if not conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name='jira_ingestion_profiles'").fetchone():
        return
    for row in conn.execute('SELECT definition FROM jira_ingestion_profiles').fetchall():
        profile = json.loads(row[0])
        if profile['log_id'] != log_id:
            continue
        try:
            staging.clear(workspace.DATA_DIR, profile['profile_id'])
        except JiraIngestionError:
            raise workspace.EventLogWorkspaceError('Temporary source data could not be erased; event log was not deleted') from None
        conn.execute('DELETE FROM jira_ingestion_manifest WHERE profile_id=?', [profile['profile_id']])
        _interrupt_credential_check(profile)
        profile.update(enabled=False, revision=profile['revision'] + 1, status='dataset_deleted',
                       error=None, checkpoint=None, last_success_at=None, run_id=None,
                       lease_until=0, retry_after=0)
        _put(conn, profile)


def _check_configuration(profile, config):
    current = configuration(os.environ)
    if current != config or not hmac.compare_digest(profile['configuration_binding'], _binding(current)):
        raise JiraIngestionError('jira_configuration_changed_recreate_profile')
    if _profile_scope(profile, current[0]) != profile['issue_keys']:
        raise JiraIngestionError('observed_jira_scope_changed')


def _guard(profile, config, *, conn=None):
    _check_configuration(profile, config)
    def check(db):
        current = _get(db, profile['profile_id'])
        if (not current['enabled'] or current['revision'] != profile['revision']
                or current.get('run_id') != profile['run_id'] or current.get('lease_until', 0) <= time.time()):
            raise JiraIngestionError('jira_run_invalidated')
        if profile['log_id']:
            try:
                workspace._check_log(db, profile['log_id'])
            except workspace.EventLogWorkspaceError:
                raise JiraIngestionError('retained_jira_dataset_deleted') from None
    if conn is not None:
        check(conn)
    else:
        with workspace._LOCK, _connect() as db:
            check(db)


def _begin(profile_id):
    config = configuration(os.environ)
    with workspace._LOCK, _connect() as conn:
        profile = _get(conn, profile_id)
        _check_configuration(profile, config)
        now = time.time()
        if profile['status'] == 'dataset_deleted':
            raise JiraIngestionError('retained_jira_dataset_deleted')
        if not profile['enabled']:
            raise JiraIngestionError('jira_profile_disabled')
        if profile.get('retry_after', 0) > now:
            raise JiraIngestionError('jira_retry_backoff_active')
        if profile.get('lease_until', 0) > now and profile.get('run_id'):
            raise JiraIngestionError('jira_run_already_active')
        staging.clear(workspace.DATA_DIR, profile_id)
        _interrupt_credential_check(profile)
        profile.update(status='running', error=None, run_id=secrets.token_hex(16),
                       lease_until=now + _LEASE_SECONDS, last_attempt_at=now)
        _put(conn, profile)
    return profile, config


def _failed(profile, error):
    with workspace._LOCK, _connect() as conn:
        try:
            current = _get(conn, profile['profile_id'])
        except JiraIngestionError:
            return
        if current['revision'] == profile['revision'] and current.get('run_id') == profile['run_id']:
            current.update(status='error', error=str(error), run_id=None, lease_until=0,
                           retry_after=time.time() + error.retry_seconds if error.retry_seconds else 0)
            _put(conn, current)


def _retain(profile, config, extraction):
    rows = extraction['rows']
    manifest = {row['event']: digest(row) for row in rows}
    if len(manifest) != len(rows):
        raise JiraIngestionError('duplicate_projected_event_identity')
    account_binding = hmac.new(ensure_api_token().encode(), extraction['account'].encode(), hashlib.sha256).hexdigest()
    if profile['account_binding'] not in {None, account_binding}:
        raise JiraIngestionError('jira_account_binding_changed')
    stream = StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(('event', 'case', 'activity', 'time'))
    writer.writerows((r['event'], r['case'], r['activity'], r['time']) for r in rows)
    mapping = {'event_id_column': 'event', 'case_column': 'case', 'activity_column': 'activity',
               'timestamp_column': 'time', 'source_namespace': profile['profile_id']}
    with workspace._LOCK:
        # Check retained history before ingest; hook checks lifecycle again in
        # the actual event/checkpoint transaction. No partial selected-case data.
        with _connect() as conn:
            _guard(profile, config, conn=conn)
            prior = dict(conn.execute('SELECT event_key, payload_sha256 FROM jira_ingestion_manifest WHERE profile_id=?',
                                      [profile['profile_id']]).fetchall())
            if any(key not in manifest for key in prior):
                raise JiraIngestionError('previously_retained_source_events_missing')
            if any(manifest[key] != value for key, value in prior.items()):
                raise JiraIngestionError('retained_source_payload_conflict')
        owned_staging = staging.prepare(workspace.DATA_DIR, profile['profile_id'])
        def checkpoint(conn, log_id):
            _guard(profile, config, conn=conn)
            now = time.time()
            saved = dict(profile)
            saved.update(log_id=log_id, account_binding=account_binding, status='read_retained', error=None,
                           run_id=None, lease_until=0, retry_after=0, last_success_at=now,
                           checkpoint={'strategy': 'bounded_full_rescan_from_zero.v1', 'events': len(rows),
                                       'histories': extraction['histories'], 'requests': extraction['requests'],
                                       'manifest_sha256': digest(sorted(manifest.items())),
                                       'provider_pagination_complete': True,
                                       'source_completeness_verified': False})
            if manifest:
                # One set-based insert, not hundreds of thousands of Python
                # prepared statements. The private temporary manifest contains
                # only opaque keys/digests and disappears after this transaction.
                with tempfile.TemporaryDirectory(prefix='owg-jira-manifest-', dir=str(owned_staging)) as folder:
                    path = Path(folder) / 'manifest.csv'
                    with path.open('w', encoding='utf-8', newline='') as output:
                        writer = csv.writer(output)
                        writer.writerow(('event_key', 'payload_sha256'))
                        writer.writerows(manifest.items())
                    conn.execute("""INSERT OR REPLACE INTO jira_ingestion_manifest
                        SELECT ?, event_key, payload_sha256 FROM read_csv(?, header=true,
                        columns={'event_key':'VARCHAR','payload_sha256':'VARCHAR'})""",
                        [profile['profile_id'], str(path)])
            _guard(profile, config, conn=conn)
            _put(conn, saved)
        try:
            source = BytesIO(stream.getvalue().encode())
            if profile['log_id']:
                result = workspace.append_log(profile['log_id'], source, source_format='csv',
                                              mapping=mapping, _transaction_hook=checkpoint, _staging_parent=owned_staging)
                added, duplicates = result['added_events'], result['duplicate_events_skipped']
            else:
                result = workspace.import_log(source, name='Selected Jira status history', source_format='csv',
                                              mapping=mapping, _transaction_hook=checkpoint, _staging_parent=owned_staging)
                added, duplicates = result['events'], 0
        except workspace.EventLogWorkspaceError:
            raise JiraIngestionError('jira_retention_refused_inspect_dataset_quality') from None
        finally:
            staging.clear(workspace.DATA_DIR, profile['profile_id'])
    # Read current state, not the stale local copy, if disable/disconnect raced
    # immediately after commit. Never return a fresh success for invalid scope.
    with workspace._LOCK, _connect() as conn:
        current = _get(conn, profile['profile_id'])
        _check_configuration(current, config)
        if current['revision'] != profile['revision'] or not current['enabled']:
            raise JiraIngestionError('jira_run_invalidated')
        return {**_public(current, time.time()), 'added_events': added, 'duplicate_events_skipped': duplicates}


def run(profile_id, *, confirm_reads, client_factory=None):
    if confirm_reads is not True:
        raise JiraIngestionError('explicit_selected_jira_read_required')
    profile, config = _begin(profile_id)
    try:
        kwargs = {'client_factory': client_factory} if client_factory else {}
        extracted = Extractor(config, lambda: _guard(profile, config), **kwargs).collect(profile['issue_keys'])
        return _retain(profile, config, extracted)
    except JiraIngestionError as error:
        _failed(profile, error)
        raise
    except Exception:
        # Never return provider, credential, SQL, status label or native ID text.
        error = JiraIngestionError('jira_ingestion_failed_retry_safe')
        _failed(profile, error)
        raise error from None
