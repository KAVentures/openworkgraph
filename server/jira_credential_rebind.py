"""Explicit same-account continuity for a configured Jira credential.

No token exchange, credential enrollment, issue reads or event-store writes.
The existing profile remains the authority for scope, account and dataset.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time

from . import jira_retained_profiles as profiles
from .jira_retained_source import configuration, JiraIngestionError

_PREVIOUS_STATUS = 'credential_check_previous_status'
_PREVIOUS_ERROR = 'credential_check_previous_error'


def _configuration(profile, config, candidate=None):
    current = configuration(os.environ)
    if current != config or (candidate is not None and not hmac.compare_digest(profiles._binding(current), candidate)):
        raise JiraIngestionError('jira_credential_configuration_changed_during_check')
    if current[:2] != (profile['host'], profile['cloud_id']):
        raise JiraIngestionError('jira_credential_tenant_change_refused')
    if profiles._profile_scope(profile, current[0]) != profile['issue_keys']:
        raise JiraIngestionError('observed_jira_scope_changed')


def _dataset(conn, profile):
    if profile['status'] == 'dataset_deleted' or not profile.get('log_id'):
        raise JiraIngestionError('jira_prior_account_and_dataset_required')
    binding = profile.get('account_binding')
    if not isinstance(binding, str) or not re.fullmatch(r'[0-9a-f]{64}', binding):
        raise JiraIngestionError('jira_prior_account_and_dataset_required')
    try:
        profiles.workspace._check_log(conn, profile['log_id'])
    except profiles.workspace.EventLogWorkspaceError:
        raise JiraIngestionError('retained_jira_dataset_deleted') from None


def _begin(profile_id, revision):
    if type(revision) is not int or revision < 1:
        raise JiraIngestionError('current_jira_profile_revision_required')
    config = configuration(os.environ)
    with profiles.workspace._LOCK, profiles._connect() as conn:
        profile = profiles._get(conn, profile_id)
        if profile['revision'] != revision:
            raise JiraIngestionError('jira_profile_revision_conflict')
        _configuration(profile, config)
        _dataset(conn, profile)
        candidate = profiles._binding(config)
        now = time.time()
        if profile.get('retry_after', 0) > now:
            raise JiraIngestionError('jira_retry_backoff_active')
        if (profile.get('credential_check_status') == 'checking'
                and profile.get('run_id') and profile.get('lease_until', 0) > now):
            raise JiraIngestionError('jira_credential_check_already_active')
        previous = profile.get(_PREVIOUS_STATUS, profile['status'])
        if previous in {'running', 'checking_credentials'}:
            previous = 'interrupted'
        profile.update(revision=revision + 1, status='checking_credentials',
                       run_id=secrets.token_hex(16), lease_until=now + profiles._LEASE_SECONDS,
                       credential_check_status='checking', credential_check_error=None,
                       credential_check_attempt_at=now,
                       **{_PREVIOUS_STATUS: previous,
                          _PREVIOUS_ERROR: profile.get(_PREVIOUS_ERROR, profile.get('error'))})
        profiles._put(conn, profile)
    return profile, config, candidate


def _guard(profile, config, candidate, *, conn=None):
    _configuration(profile, config, candidate)
    def check(db):
        current = profiles._get(db, profile['profile_id'])
        if (current['revision'] != profile['revision'] or current.get('run_id') != profile['run_id']
                or current.get('lease_until', 0) <= time.time()
                or current['status'] != 'checking_credentials'
                or current['enabled'] != profile['enabled']
                or current['configuration_binding'] != profile['configuration_binding']
                or current['account_binding'] != profile['account_binding']):
            raise JiraIngestionError('jira_credential_check_invalidated')
        _dataset(db, current)
    if conn is not None:
        check(conn)
    else:
        with profiles.workspace._LOCK, profiles._connect() as db:
            check(db)


def _restore_history(current):
    current['status'] = current.pop(_PREVIOUS_STATUS)
    current['error'] = current.pop(_PREVIOUS_ERROR)
    current.update(run_id=None, lease_until=0)


def _failed(profile, error):
    with profiles.workspace._LOCK, profiles._connect() as conn:
        try:
            current = profiles._get(conn, profile['profile_id'])
        except JiraIngestionError:
            return
        if current['revision'] == profile['revision'] and current.get('run_id') == profile['run_id']:
            _restore_history(current)
            current.update(credential_check_status='failed', credential_check_error=str(error))
            if error.retry_seconds:
                current['retry_after'] = max(current.get('retry_after', 0), time.time() + error.retry_seconds)
            profiles._put(conn, current)


def _commit(profile, config, candidate, account):
    account_binding = hmac.new(profiles.ensure_api_token().encode(), account.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(profile['account_binding'], account_binding):
        raise JiraIngestionError('jira_credential_account_mismatch')
    with profiles.workspace._LOCK, profiles._connect() as conn:
        conn.execute('BEGIN')
        try:
            _guard(profile, config, candidate, conn=conn)
            current = profiles._get(conn, profile['profile_id'])
            binding = candidate
            generation = current.get('credential_generation', 1)
            if not hmac.compare_digest(current['configuration_binding'], binding):
                generation += 1
            _restore_history(current)
            current.update(configuration_binding=binding, credential_generation=generation,
                           credential_verified_at=time.time(), credential_check_status='verified',
                           credential_check_error=None)
            profiles._put(conn, current)
            _configuration(profile, config, candidate)
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
    with profiles.workspace._LOCK, profiles._connect() as conn:
        latest = profiles._get(conn, profile['profile_id'])
        profiles._check_configuration(latest, config)
        if latest['revision'] != profile['revision']:
            raise JiraIngestionError('jira_credential_check_invalidated')
        return profiles._public(latest, time.time())


def rebind(profile_id, *, revision, confirm_account_check, client_factory=None):
    if confirm_account_check is not True:
        raise JiraIngestionError('explicit_jira_account_check_confirmation_required')
    profile, config, candidate = _begin(profile_id, revision)
    try:
        kwargs = {'client_factory': client_factory} if client_factory else {}
        account = profiles.Extractor(config, lambda: _guard(profile, config, candidate), **kwargs).verify_account()
        return _commit(profile, config, candidate, account)
    except JiraIngestionError as error:
        _failed(profile, error)
        raise
    except Exception:
        error = JiraIngestionError('jira_credential_check_failed_retry_safe')
        _failed(profile, error)
        raise error from None
