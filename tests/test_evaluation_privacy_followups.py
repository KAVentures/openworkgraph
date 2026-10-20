from copy import deepcopy

import pytest

from collector.privacy import should_exclude
from server.capture_exclusions import apply_to_desktop_event
from shared.core.private_browsing import private_browser_window, suppress_private_browsing
from shared.core.sensitive_identifiers import sanitize_event_identifiers
from shared.evidence import rich_evidence_row
from gateway.plugin_redaction import project


@pytest.mark.parametrize('marker', ['Inkognito', 'Incognito', 'inkognito', 'InPrivate'])
@pytest.mark.parametrize('event_type', ['focus_span', 'screen_click', 'clipboard_copy', 'clipboard_paste'])
def test_private_window_drops_all_content_before_capture_and_on_legacy_reads(marker, event_type):
    event = {'event_id': 'opaque-event', 'session_id': 'opaque-session',
             'observed_at': '2026-10-20T10:00:00Z', 'duration_seconds': 12,
             'app': 'Google Chrome', 'window_title': f'SECRET_TITLE - Google Chrome ({marker})',
             'source': 'desktop', 'event_type': event_type,
             'screenshot_path': '/private/SECRET_FILE.png',
             'metadata': {'target': {'label': 'SECRET_TARGET'},
                          'page': {'hostname': 'secret.invalid'},
                          'file_reference': {'file_ref': 'SECRET_FILE'},
                          'clipboard_transfer_id': 'SECRET_CLIPBOARD'}}
    original = deepcopy(event)
    assert should_exclude(event['app'], event['window_title'], [], [])
    for safe in [apply_to_desktop_event(event, {}), sanitize_event_identifiers(event),
                 rich_evidence_row(event), suppress_private_browsing({'rows': [event]}),
                 project({'rows': [event]}, secret='test', principal='test', detail='rich')]:
        assert 'SECRET' not in str(safe)
        assert 'secret.invalid' not in str(safe)
        assert 'opaque-event' in str(safe)
    assert event == original
    safe = suppress_private_browsing(event)
    assert safe['duration_seconds'] == 12
    assert safe['session_id'] == 'opaque-session'


@pytest.mark.parametrize('app,title', [
    ('Google Chrome', 'Inkognito brand pricing - Google Search'),
    ('Google Chrome', 'Private equity report - Google Chrome'),
    ('Preview', 'Research (Inkognito)'),
    ('Google Chrome', 'GitHub pull request #42'),
])
def test_regular_work_context_and_references_are_preserved(app, title):
    event = {'app': app, 'window_title': title,
             'metadata': {'resource_reference': {'resource_ref': 'owg:r:opaque-work'}}}
    assert not private_browser_window(app, title)
    assert suppress_private_browsing(event) == event
    assert not should_exclude(app, title, [], [])


@pytest.mark.parametrize('title_field', ['resource_title', 'context_title', 'page_title'])
def test_legacy_derived_context_keeps_schema_without_private_content(title_field):
    event = {'event_id': 'context-1', 'surface': 'Gmail',
             title_field: 'SECRET_TITLE - Google Chrome (Inkognito)',
             'target_label': 'SECRET_TARGET', 'context_text': 'SECRET_BODY',
             'resource_locator': 'https://secret.invalid/record'}
    safe = suppress_private_browsing(event)
    assert safe['surface'] == 'Excluded'
    assert safe[title_field] == ''
    assert safe['event_id'] == 'context-1'
    assert 'SECRET_' not in str(safe) and 'secret.invalid' not in str(safe)


def test_dashboard_activity_hides_legacy_private_context_before_title_formatting():
    from server.evidence_query import _row_item
    row = {'event_id': 'legacy-context', 'observed_at': '2026-10-20T10:00:00Z',
           'surface': 'Gmail', 'resource_title': 'SECRET_TITLE - Google Chrome (Inkognito)',
           'target_label': 'SECRET_TARGET', 'action': 'click', 'metadata_json': '{}'}
    safe = _row_item(row)
    assert safe['surface'] == 'Excluded'
    assert safe['page'] == 'Excluded'
    assert safe['resource_title'] == ''
    assert safe['event_id'] == 'legacy-context'
    assert 'SECRET_' not in str(safe)
