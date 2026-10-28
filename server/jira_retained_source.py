"""Bounded selected-issue extraction. Does not persist or alter source receipts."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

import httpx

from .jira_source_evidence import _CLOUD_ID, _ISSUE, _TENANT
from .source_http_logging import private_source_http_logs

ROOT = 'https://api.atlassian.com'
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_BYTES = 512 * 1024
MAX_ITEMS = 30
MAX_SECONDS = 70
_ID = re.compile(r'^[1-9][0-9]{0,19}$')
_STATUS = re.compile(r'^[A-Za-z0-9:._-]{1,128}$')


class JiraIngestionError(ValueError):
    """Fixed, content-free diagnostic, optionally carrying bounded backoff."""
    def __init__(self, reason, retry_seconds=0):
        super().__init__(reason)
        self.retry_seconds = retry_seconds


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError('nonfinite JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def configuration(env):
    if (str(env.get('OWG_JIRA_SOURCE_READ_ENABLED', '')).lower() not in {'true', '1', 'yes', 'on'}
            or env.get('OWG_JIRA_RETAINED_INGESTION_ENABLED') != 'true'):
        raise JiraIngestionError('retained_jira_connector_disabled')
    host = str(env.get('OWG_JIRA_HOST', '')).strip().lower()
    cloud = str(env.get('OWG_JIRA_CLOUD_ID', '')).strip().lower()
    token = str(env.get('OWG_JIRA_OAUTH_ACCESS_TOKEN', '')).strip()
    if (not _TENANT.fullmatch(host) or not _CLOUD_ID.fullmatch(cloud)
            or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise JiraIngestionError('jira_configuration_unavailable')
    return host, cloud, token


def stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise JiraIngestionError('invalid_source_timestamp')
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if at.utcoffset() is None:
            raise ValueError()
        at = at.astimezone(timezone.utc)
        if not 1900 <= at.year <= 2100:
            raise ValueError()
        return at.isoformat()
    except (ValueError, OverflowError):
        raise JiraIngestionError('invalid_source_timestamp') from None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise JiraIngestionError('invalid_source_identity')
    return value


def _label(value):
    # Labels are explicitly retained business content; never truncate to a
    # different activity. Control characters and unsupported sizes refuse.
    if (not isinstance(value, str) or not value.strip() or len(value) > 160
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise JiraIngestionError('invalid_status_label')
    return value


class Extractor:
    def __init__(self, config, guard, *, client_factory=httpx.Client, clock=time.monotonic):
        self.host, self.cloud, self.token = config
        self.guard = guard
        self.factory = client_factory
        self.clock = clock
        self.deadline = clock() + MAX_SECONDS
        self.requests = 0

    def raw(self, path, params=None):
        self.guard()
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise JiraIngestionError('source_read_budget_exceeded')
        self.requests += 1
        with self.client.stream('GET', ROOT + path, params=params,
                timeout=min(8, remaining), headers={'Authorization': 'Bearer ' + self.token,
                                                    'Accept': 'application/json'}) as response:
            if response.status_code in {401, 403}:
                raise JiraIngestionError('source_access_denied')
            if response.status_code == 429:
                retry = response.headers.get('Retry-After', '')
                delay = min(3600, max(60, int(retry))) if retry.isascii() and retry.isdigit() and len(retry) <= 6 else 60
                raise JiraIngestionError('source_rate_limited', delay)
            if response.status_code == 404:
                raise JiraIngestionError('selected_issue_unavailable')
            if response.status_code != 200:
                raise JiraIngestionError('source_request_failed')
            raw = bytearray()
            for chunk in response.iter_bytes():
                if self.clock() >= self.deadline:
                    raise JiraIngestionError('source_read_budget_exceeded')
                if len(raw) + len(chunk) > MAX_BYTES:
                    raise JiraIngestionError('source_response_too_large')
                raw.extend(chunk)
            try:
                value = strict_json(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise JiraIngestionError('invalid_source_json') from None
            finally:
                raw.clear()
        self.guard()
        return value

    def grant(self):
        grants = self.raw('/oauth/token/accessible-resources')
        if not isinstance(grants, list) or len(grants) > 1000:
            raise JiraIngestionError('invalid_site_grants')
        matches = [g for g in grants if isinstance(g, dict) and g.get('id') == self.cloud
                   and g.get('url') in {'https://' + self.host, 'https://' + self.host + '/'}
                   and isinstance(g.get('scopes'), list)
                   and {'read:jira-work', 'read:jira-user'}.issubset(set(s for s in g['scopes'] if isinstance(s, str)))]
        if len(matches) != 1:
            raise JiraIngestionError('jira_site_or_read_scopes_not_granted')

    def get(self, suffix, params=None):
        # Never follow self/nextPage URLs, redirects, arbitrary hosts or keys.
        self.grant()
        return self.raw('/ex/jira/' + self.cloud + '/rest/api/3/' + suffix, params)

    def account(self):
        me = self.get('myself')
        if (not isinstance(me, dict) or not isinstance(me.get('accountId'), str)
                or not re.fullmatch(r'[A-Za-z0-9:._-]{1,128}', me['accountId'])
                or me.get('active') is not True):
            raise JiraIngestionError('jira_account_unavailable')
        return me['accountId']

    def issue(self, key):
        if not _ISSUE.fullmatch(key):
            raise JiraIngestionError('invalid_selected_issue')
        value = self.get('issue/' + key, {'fields': 'created,updated'})
        if not isinstance(value, dict) or value.get('key') != key or not isinstance(value.get('fields'), dict):
            raise JiraIngestionError('issue_identity_mismatch')
        result = (_identifier(value.get('id')), stamp(value['fields'].get('created')),
                  stamp(value['fields'].get('updated')))
        if datetime.fromisoformat(result[1]) > datetime.fromisoformat(result[2]):
            raise JiraIngestionError('issue_timestamp_conflict')
        return result

    def history(self, key, native_id, created, updated):
        rows = [{'event': 'created:' + native_id, 'case': native_id,
                 'activity': 'Issue created', 'time': created}]
        seen, offset, total = set(), 0, None
        for _ in range(MAX_PAGES):
            page = self.get('issue/' + key + '/changelog', {'startAt': offset, 'maxResults': PAGE_SIZE})
            if (not isinstance(page, dict) or type(page.get('startAt')) is not int
                    or page['startAt'] != offset or type(page.get('maxResults')) is not int
                    or not 1 <= page['maxResults'] <= PAGE_SIZE or type(page.get('total')) is not int
                    or not 0 <= page['total'] <= MAX_PAGES * PAGE_SIZE
                    or type(page.get('isLast')) is not bool or not isinstance(page.get('values'), list)
                    or len(page['values']) > page['maxResults']):
                raise JiraIngestionError('invalid_history_pagination')
            if total is not None and total != page['total']:
                raise JiraIngestionError('source_history_changed_during_read')
            total = page['total']
            end = offset + len(page['values'])
            if end > total or page['isLast'] != (end == total) or (end < total and end == offset):
                raise JiraIngestionError('contradictory_history_pagination')
            for history in page['values']:
                if not isinstance(history, dict):
                    raise JiraIngestionError('invalid_history')
                history_id = _identifier(history.get('id'))
                if history_id in seen:
                    raise JiraIngestionError('duplicate_history_identity')
                seen.add(history_id)
                at = stamp(history.get('created'))
                if not datetime.fromisoformat(created) <= datetime.fromisoformat(at) <= datetime.fromisoformat(updated):
                    raise JiraIngestionError('history_outside_issue_time_bounds')
                items = history.get('items')
                if not isinstance(items, list) or len(items) > MAX_ITEMS:
                    raise JiraIngestionError('unsupported_history_item_count')
                occurrences = {}
                # Sort only the projected payload. Unrelated item contents do
                # not affect status-event identity or ordinal duplicate slots.
                for item in sorted(items, key=lambda i: digest([i.get('fieldId'), i.get('field'), i.get('from'), i.get('to'), i.get('toString')]) if isinstance(i, dict) else ''):
                    if not isinstance(item, dict):
                        raise JiraIngestionError('invalid_history_item')
                    if item.get('fieldId') != 'status' and not (
                            'fieldId' not in item and item.get('field') == 'status' and item.get('fieldtype') == 'jira'):
                        continue
                    before, after = item.get('from'), item.get('to')
                    if (not isinstance(before, str) or not _STATUS.fullmatch(before)
                            or not isinstance(after, str) or not _STATUS.fullmatch(after) or before == after):
                        raise JiraIngestionError('invalid_status_transition')
                    # Semantic item identity survives array reordering. Exact
                    # duplicates get distinct occurrence slots. The labels are
                    # payload, so corrections conflict rather than overwrite.
                    identity = digest([native_id, history_id, 'status', before, after])
                    occurrence = occurrences.get(identity, 0)
                    occurrences[identity] = occurrence + 1
                    rows.append({'event': identity + ':' + str(occurrence), 'case': native_id,
                                 'activity': 'Status: ' + _label(item.get('toString')),
                                 'time': at})
            offset = end
            if page['isLast']:
                return rows, total
        raise JiraIngestionError('history_page_budget_exceeded')

    def collect(self, issue_keys):
        rows, histories, native_ids = [], 0, set()
        try:
            with private_source_http_logs(), self.factory(follow_redirects=False, trust_env=False) as self.client:
                account = self.account()
                for key in issue_keys:
                    before = self.issue(key)
                    if before[0] in native_ids:
                        raise JiraIngestionError('selected_issue_alias_collision')
                    native_ids.add(before[0])
                    events, count = self.history(key, before[0], before[1], before[2])
                    if self.issue(key) != before:
                        raise JiraIngestionError('issue_changed_during_read')
                    rows.extend(events)
                    histories += count
                if self.account() != account:
                    raise JiraIngestionError('jira_account_changed_during_read')
                self.guard()
            return {'account': account, 'rows': rows, 'histories': histories,
                    'requests': self.requests}
        except (httpx.HTTPError, OSError):
            raise JiraIngestionError('source_request_failed') from None
