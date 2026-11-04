"""DMN decision-table interchange limited to the exactly mappable subset (#370 R21)."""
from copy import deepcopy
from xml.sax.saxutils import escape

import pytest

from shared.process_decision_tables import evaluate_table
from shared.process_dmn import DMN, DMNError, export_dmn, import_dmn, unary_test_to_condition
from shared.process_model import ProcessModel


def graph():
    return {'goal': 'Review an authorized purchase', 'baseline_source': 'user_declared',
            'baseline_path': ['Review', 'Accept'], 'baseline_coverage': 'user_declared_design',
            'activities': [{'id': 'choose', 'label': 'Review', 'kind': 'decision'},
                           {'id': 'accept', 'label': 'Accept'}, {'id': 'escalate', 'label': 'Escalate'},
                           {'id': 'reject', 'label': 'Reject'}],
            'transitions': [{'source': 'choose', 'target': 'accept', 'rule_id': 'small'},
                            {'source': 'choose', 'target': 'escalate', 'rule_id': 'large'},
                            {'source': 'choose', 'target': 'reject', 'rule_id': 'blocked'}],
            'entry_activity_id': 'choose', 'terminal_activity_ids': ['accept', 'escalate', 'reject'],
            'business_rules': [
                {'id': 'small', 'text': 'Small purchase from an approved vendor', 'user_confirmed': True,
                 'predicate': {'conditions': [{'path': '/amount', 'operator': 'lt', 'expected': 100},
                                              {'path': '/vendor/status', 'operator': 'in', 'expected': ['approved', 'preferred']}]}},
                {'id': 'large', 'text': 'Large purchase', 'user_confirmed': True,
                 'predicate': {'conditions': [{'path': '/amount', 'operator': 'gte', 'expected': 100},
                                              {'path': '/vendor/status', 'operator': 'ne', 'expected': 'blocked'}]}},
                {'id': 'blocked', 'text': 'Blocked vendor', 'user_confirmed': True,
                 'predicate': {'conditions': [{'path': '/vendor/status', 'operator': 'eq', 'expected': 'blocked'}]}}],
            'decision_tables': [{'id': 'purchase', 'decision_activity_id': 'choose', 'hit_policy': 'FIRST',
                                 'effective_from': '2026-01-01', 'effective_until': '2027-01-01',
                                 'source_basis': {'label': 'Purchasing policy', 'document_sha256': 'a' * 64},
                                 'rows': [{'id': 'blk', 'rule_id': 'blocked', 'target_activity_id': 'reject'},
                                          {'id': 'low', 'rule_id': 'small', 'target_activity_id': 'accept'},
                                          {'id': 'high', 'rule_id': 'large', 'target_activity_id': 'escalate'}],
                                 'user_confirmed': True}]}


def packet(value=None):
    return {'workflow_id': 'purchase_process', 'revision': 3, 'content_sha256': 'b' * 64,
            'process_model': ProcessModel.model_validate(value or graph()).model_dump()}


@pytest.mark.parametrize('text,op,expected', [
    ('5', 'eq', 5), ('"gold"', 'eq', 'gold'), ('true', 'eq', True), ('> 5', 'gt', 5), ('>=2.5', 'gte', 2.5),
    ('< -1', 'lt', -1), ('<= 10', 'lte', 10), ('not("x")', 'ne', 'x'), ('"a", "b"', 'in', ['a', 'b']),
    ('not("a","b")', 'not_in', ['a', 'b']), ('null', 'not_exists', None), ('not(null)', 'exists', None),
    ('"say \\"hi\\""', 'eq', 'say "hi"'),
])
def test_simple_unary_tests_map_to_owg_conditions(text, op, expected):
    condition = unary_test_to_condition('customer.tier', text)
    assert condition.path == '/customer/tier' and condition.operator == op and condition.expected == expected


@pytest.mark.parametrize('text', ['[1..5]', 'date("2026-01-01")', '> "a"', 'amount + 1', '"unterminated', 'x'])
def test_full_feel_is_refused(text):
    with pytest.raises((DMNError, ValueError)):
        unary_test_to_condition('amount', text)


def test_export_writes_a_dmn_table_and_round_trips_exactly():
    exported = export_dmn(packet())
    assert DMN in exported and 'hitPolicy="FIRST"' in exported and '"Reject"' in exported
    preview = import_dmn(exported.encode(), packet()['process_model'], 'choose', effective_from='2026-01-01')
    assert preview['hit_policy'] == 'FIRST' and preview['process_model_saved'] is False
    proposed = ProcessModel.model_validate(preview['proposed_process_model'])
    [table] = proposed.decision_tables
    assert table.user_confirmed is False and not any(r.user_confirmed for r in proposed.business_rules)
    assert [row.target_activity_id for row in table.rows] == ['reject', 'accept', 'escalate']
    original = ProcessModel.model_validate(graph())
    for facts in ({'amount': 50, 'vendor': {'status': 'approved'}}, {'amount': 500, 'vendor': {'status': 'new'}},
                  {'amount': 50, 'vendor': {'status': 'blocked'}}, {'amount': 50, 'vendor': {'status': 'new'}}, {}):
        def run(model):
            t = deepcopy(model.decision_tables[0]).model_copy(update={'user_confirmed': True})
            rules = {r.id: r.model_copy(update={'user_confirmed': True}) for r in model.business_rules}
            return evaluate_table(t, rules, facts, '2026-06-01')
        a, b = run(original), run(proposed)
        assert (a['status'], a['selected_target_activity_id']) == (b['status'], b['selected_target_activity_id'])


def test_export_refuses_what_dmn_rows_cannot_hold():
    value = graph()
    value['business_rules'][2]['predicate'] = {'match': 'any', 'conditions': [
        {'path': '/vendor/status', 'operator': 'eq', 'expected': 'blocked'},
        {'path': '/amount', 'operator': 'gt', 'expected': 10 ** 6}]}
    with pytest.raises(DMNError, match='ANY'):
        export_dmn(packet(value))
    value = graph()
    value['business_rules'][0]['predicate']['conditions'][1]['path'] = '/vendor/status-code'
    with pytest.raises(DMNError, match='simple fact paths'):
        export_dmn(packet(value))
    value = graph()
    value.pop('decision_tables')
    with pytest.raises(DMNError):
        export_dmn(packet(value))


def _dmn(rules, hit='UNIQUE', outputs=1):
    out = ''.join(f'<output id="o{i}" typeRef="string"/>' for i in range(outputs))
    body = ''.join(f'<rule id="r{i}"><inputEntry id="i{i}"><text>{escape(entry)}</text></inputEntry>'
                   f'<outputEntry id="e{i}"><text>"{target}"</text></outputEntry></rule>'
                   for i, (entry, target) in enumerate(rules))
    return (f'<definitions xmlns="{DMN}" id="d" name="d" namespace="x"><decision id="dec" name="Route">'
            f'<decisionTable id="t" hitPolicy="{hit}"><input id="in"><inputExpression id="ie"><text>amount</text>'
            f'</inputExpression></input>{out}{body}</decisionTable></decision></definitions>').encode()


def _three_way():
    value = graph()
    value.pop('decision_tables')
    return ProcessModel.model_validate(value).model_dump()


def test_import_from_another_tool_proposes_unconfirmed_rules():
    raw = _dmn([('< 100', 'Accept'), ('>= 100', 'Escalate'), ('null', 'Reject')])
    preview = import_dmn(raw, _three_way(), 'choose', effective_from='2026-03-01')
    table = preview['proposed_process_model']['decision_tables'][0]
    assert table['source_basis']['label'] == 'DMN Route' and len(table['source_basis']['document_sha256']) == 64
    assert table['effective_from'] == '2026-03-01' and table['user_confirmed'] is False
    edges = {e['target']: e['rule_id'] for e in preview['proposed_process_model']['transitions']}
    assert set(edges.values()) == {r['id'] for r in preview['proposed_rules']}


@pytest.mark.parametrize('raw', [
    _dmn([('< 100', 'Accept'), ('>= 100', 'Escalate'), ('null', 'Reject')], hit='COLLECT'),
    _dmn([('< 100', 'Accept'), ('>= 100', 'Escalate'), ('null', 'Reject')], outputs=2),
    _dmn([('< 100', 'Accept'), ('>= 100', 'Unknown step'), ('null', 'Reject')]),
    _dmn([('< 100', 'Accept'), ('>= 100', 'Escalate')]),
    _dmn([('< 100', 'Accept'), ('>= 100', 'Accept'), ('null', 'Reject')]),
    _dmn([('-', 'Accept'), ('>= 100', 'Escalate'), ('null', 'Reject')]),
    _dmn([('[1..5]', 'Accept'), ('>= 100', 'Escalate'), ('null', 'Reject')]),
    b'<!DOCTYPE x [<!ENTITY e "boom">]><definitions/>',
    b'<definitions xmlns="http://example.com/other"/>',
])
def test_import_refuses_unmappable_or_unsafe_dmn(raw):
    with pytest.raises(DMNError):
        import_dmn(raw, _three_way(), 'choose', effective_from='2026-03-01')


def test_import_requires_a_decision_step_and_valid_dates():
    raw = _dmn([('< 100', 'Accept'), ('>= 100', 'Escalate'), ('null', 'Reject')])
    with pytest.raises(DMNError):
        import_dmn(raw, _three_way(), 'accept', effective_from='2026-03-01')
    with pytest.raises(DMNError):
        import_dmn(raw, _three_way(), 'choose', effective_from='March 1')


def test_export_and_import_routes(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.local_process_catalog import catalog
from shared.process_catalog import CatalogWrite
saved = catalog.save(CatalogWrite(title="Purchase", process_model=json.loads(MODEL), user_confirmed=True))
wid, rev = saved["workflow_id"], saved["revision"]
owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
with TestClient(app) as client:
    exported = client.get(f"/v1/process-catalog/export?workflow_id={wid}&revision={rev}&format=dmn", headers=owner)
    assert exported.status_code == 200 and b"decisionTable" in exported.content, exported.text
    query = f"?workflow_id={wid}&revision={rev}&decision_activity_id=choose&effective_from=2026-05-01"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token(), "Content-Type": "application/xml"}
    assert client.post("/v1/process-catalog/import-dmn" + query, content=exported.content, headers=bearer).status_code == 403
    preview = client.post("/v1/process-catalog/import-dmn" + query, content=exported.content,
                          headers={**owner, "Content-Type": "application/xml"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["based_on"]["revision"] == rev and body["process_model_saved"] is False
    assert client.post("/v1/process-catalog/import-dmn" + query, content=b"<x/>",
                       headers={**owner, "Content-Type": "text/plain"}).status_code == 415
    assert catalog.get(wid)["revision"] == rev
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path / "data"), WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path / "auth"),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path / "config.json"))
    script = f"MODEL = {json.dumps(json.dumps(graph()))}\n" + code
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
