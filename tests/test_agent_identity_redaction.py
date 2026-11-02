"""Synthetic generation, privacy and persistence regressions for issue #490."""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from server import agent_session_store as identities
from shared.core.sensitive_identifiers import redact_sensitive_identifiers, sanitize_event_identifiers

BAD_DIGEST = 'fb6b48c4579456233864e368' + 'a' * 40
KEY = b'synthetic-agent-reference-test-key'


@pytest.fixture
def local(tmp_path, monkeypatch):
    from server import db

    auth, data = tmp_path / 'auth', tmp_path / 'data'
    auth.mkdir()
    data.mkdir()
    monkeypatch.setenv('WORKFLOW_OBSERVER_AUTH_DIR', str(auth))
    monkeypatch.setenv('WORKFLOW_OBSERVER_DATA', str(data))
    monkeypatch.setattr(db, 'DB_PATH', data / 'events.db')
    monkeypatch.setattr(identities, '_secret', lambda: KEY)
    db.init_db()
    return data


def force_initial_collision(monkeypatch):
    """Force the reported numeric collision, using real HMAC for all retries."""
    real_new = hmac.new

    def new(key, material, digestmod):
        if not material.startswith(b'\x00owg-agent-ref-redaction-v1\x00'):
            return SimpleNamespace(hexdigest=lambda: BAD_DIGEST)
        return real_new(key, material, digestmod)

    # Replace only this module's binding, not the shared global hmac module.
    monkeypatch.setattr(identities, 'hmac', SimpleNamespace(new=new))


@pytest.mark.parametrize('prefix,chars', [('as', 20), ('ar', 24), ('at', 24), ('af', 20), ('am', 24)])
def test_reported_collision_is_deterministic_redaction_stable(local, monkeypatch, prefix, chars):
    initial = prefix + ':' + BAD_DIGEST[:chars]
    assert redact_sensitive_identifiers(initial) != initial
    force_initial_collision(monkeypatch)
    material = b'\x00owg-agent-ref-redaction-v1\x001\x00native-synthetic-id'
    expected = prefix + ':' + hmac.new(KEY, material, hashlib.sha256).hexdigest()[:chars]
    assert redact_sensitive_identifiers(expected) == expected
    actual = identities._opaque(prefix, 'native-synthetic-id', chars=chars)
    assert actual == expected == identities._opaque(prefix, 'native-synthetic-id', chars=chars)
    assert actual != initial
    assert len(actual) == len(initial)
    assert set(actual.split(':')[1]) <= set('0123456789abcdef')


@pytest.mark.parametrize('prefix,chars', [('as', 20), ('ar', 24), ('at', 24), ('af', 20), ('am', 24)])
def test_noncolliding_historical_hmac_bytes_are_unchanged(local, prefix, chars):
    for native in ['ordinary-native-id', 'literal café|session|turn', 'ar:4111111111111111abcdefab']:
        expected = prefix + ':' + hmac.new(KEY, native.encode(), hashlib.sha256).hexdigest()[:chars]
        assert redact_sensitive_identifiers(expected) == expected
        assert identities._opaque(prefix, native, chars=chars) == expected


def test_retries_are_bounded_and_fail_closed(local, monkeypatch):
    attempts = []

    def broken(key, material, digestmod):
        attempts.append(material)
        return SimpleNamespace(hexdigest=lambda: BAD_DIGEST)

    monkeypatch.setattr(identities, 'hmac', SimpleNamespace(new=broken))
    with pytest.raises(ValueError, match='privacy-stable agent reference'):
        identities._opaque('ar', 'native-synthetic-id', chars=24)
    assert len(attempts) == len(set(attempts)) == 64


def test_a_second_collision_is_not_returned(local, monkeypatch):
    real_new, calls = hmac.new, []

    def new(key, material, digestmod):
        calls.append(material)
        if len(calls) <= 2:
            return SimpleNamespace(hexdigest=lambda: BAD_DIGEST)
        return real_new(key, material, digestmod)

    monkeypatch.setattr(identities, 'hmac', SimpleNamespace(new=new))
    material = b'\x00owg-agent-ref-redaction-v1\x002\x00native'
    expected = 'ar:' + real_new(KEY, material, hashlib.sha256).hexdigest()[:24]
    assert identities._opaque('ar', 'native', chars=24) == expected
    assert len(calls) == 3


@pytest.mark.parametrize('forged', [
    'ar:fb6b48c4579456233864e368',
    'ar:4111111111111111abcdefab',
    'at:4111111111111111abcdefab',
    'as:4111111111111111abcd',
])
def test_opaque_looking_input_has_no_redaction_exemption(local, forged):
    safe = sanitize_event_identifiers({'metadata': {'trace': {'run_id': forged},
        'adjacent': 'card 4111111111111111; password=secretfixture123'}})
    assert safe['metadata']['trace']['run_id'] != forged
    assert '4111111111111111' not in json.dumps(safe)
    assert '4579456233864' not in json.dumps(safe)
    assert 'secretfixture123' not in json.dumps(safe)


@pytest.mark.parametrize('spooled', [False, True])
def test_generated_hook_identity_survives_actual_ingest_and_spool(local, monkeypatch, spooled):
    from server import agent_spool as spool, db
    from server.agent_identity import normalize_agent_events
    from server.agent_ingest import ingest_agent_payloads
    from shared import capture_control
    from shared.claude_code_adapter import claude_hook_to_agent_events

    force_initial_collision(monkeypatch)
    started = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(capture_control, '_now', lambda: started)
    capture_control.initialize_run(started)
    raw = claude_hook_to_agent_events({'session_id': 'synthetic-session',
        'prompt_id': 'synthetic-turn', 'hook_event_name': 'PostToolUse',
        'tool_use_id': 'synthetic-tool', 'tool_name': 'Read'},
        observed_at=datetime.now(timezone.utc).isoformat())
    normalized = normalize_agent_events(raw)
    [item] = normalized
    assert item['run_id'] != 'ar:fb6b48c4579456233864e368'
    assert normalize_agent_events(json.loads(json.dumps(normalized))) == normalized
    if spooled:
        spool.issue_lease(local, lease_id='synthetic-identity-test')
        assert spool.spool_events(normalized)
        flushed = spool.flush_spool(local, ingest_agent_payloads)
        assert flushed['delivered_events'] == 1
        assert spool.pending_count() == 0
    else:
        assert ingest_agent_payloads(raw)['inserted'] == 1
    for table in ('events', 'normalized_events', 'context_events'):
        [stored] = db.rows('SELECT session_id,metadata_json FROM ' + table)
        assert stored['session_id'] == item['session_id']
        trace = stored['metadata']['trace']
        for field in ('run_id', 'trace_id', 'span_id'):
            assert trace[field] == item[field]
        assert 'synthetic-turn' not in json.dumps(stored)
        assert 'synthetic-tool' not in json.dumps(stored)
        from server.ai_context import redact_contextually
        from server.privacy_pipeline import redact_for_display_now
        for display in (redact_for_display_now(stored), redact_contextually(stored)):
            assert display['metadata']['trace']['run_id'] == item['run_id']
            assert display['metadata']['trace']['span_id'] == item['span_id']
    # The same tool in the native-session fallback uses the same issued span.
    from server.agent_session_sensor import _claude_tool_span
    assert _claude_tool_span(item['session_id'], 'synthetic-tool') == item['span_id']


def test_existing_normalizer_does_not_trust_a_forged_prefixed_run(local):
    from server.agent_identity import normalize_agent_event
    from server.agent_ingest import ingest_agent_payloads
    from server import db

    forged = 'ar:4111111111111111abcdefab'
    event = {'agent_name': 'Claude Code', 'framework': 'claude-code', 'provider': 'anthropic',
        'session_id': 'synthetic-session', 'run_id': forged, 'operation': 'run_started',
        'observed_at': datetime.now(timezone.utc).isoformat()}
    # The existing idempotent prefix behavior is unchanged, not a trust grant.
    assert normalize_agent_event(event)['run_id'] == forged
    assert ingest_agent_payloads([event])['inserted'] == 1
    [row] = db.rows('SELECT metadata_json FROM events')
    assert row['metadata']['trace']['run_id'] != forged
    assert '4111111111111111' not in json.dumps(row)
