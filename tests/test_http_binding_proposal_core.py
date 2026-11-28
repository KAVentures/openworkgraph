import importlib.machinery
import importlib.util
from pathlib import Path
from itertools import product
import pytest

path = Path(__file__).resolve().parents[1] / 'shared/process_agent_templates/propose_http_bindings.py.txt'
loader = importlib.machinery.SourceFileLoader('propose_http_bindings', str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


def inputs():
    rows = [{'activity_id': 'a', 'operation_name': 'write', 'relevant_cases': 2,
             'arguments': [{'argument': 'id', 'full_support_paths': ['/id', '/other']},
                           {'argument': 'key', 'full_support_paths': ['/key']}]}]
    activities = {'a': {'implementation': 'api'}}
    operations = {'write': {'method': 'PATCH', 'fields': {'id': 'integer', 'key': 'string'}}}
    schemas = {'write': {'type': 'object'}}
    cases = [{'input': {'id': i, 'other': i+1, 'key': 'PRIVATE'}, 'expected': {'accepted_routes': routes}}
             for i, routes in enumerate([[['a'], ['a', 'a']], [['a']], [['b']], []])]
    return rows, activities, operations, schemas, cases


def test_explicit_choices_and_combined_request_checks():
    rows, activities, operations, schemas, cases = inputs()
    for field in ('/id', '/other'):
        calls = []
        choices = {'a': {'id': field, 'key': '/key'}}
        result, checks = m.assemble(rows, choices, activities, operations, schemas, cases,
            request=lambda name, mapping, data: calls.append((name, data['id'])), digest=lambda x: 'pin')
        assert calls == [('write', 0), ('write', 1)]
        assert checks == [{'activity_id': 'a', 'combined_request_checks_passed': 2}]
        assert result[0]['argument_map'] == choices['a']
        assert result[0]['operation_kind'] == 'unmapped' and result[0]['authorized'] is False
        assert result[0]['proposed_operation_kind'] == 'write'
        assert all(result[0][key] is None for key in ('precondition','idempotency','concurrency_control','result_pointer'))
        assert 'PRIVATE' not in str(result)


def test_exact_choice_population():
    rows, activities, operations, schemas, cases = inputs()
    for choices in ({}, {'b': {}}, {'a': {}}, {'a': {'id': '/id'}},
                    {'a': {'id': '/missing', 'key': '/key'}},
                    {'a': {'id': '/id', 'key': '/key', 'extra': '/id'}},
                    {'a': {'id': True, 'key': '/key'}}):
        with pytest.raises(ValueError):
            m.assemble(rows, choices, activities, operations, schemas, cases,
                       request=lambda *a: pytest.fail('must refuse before request'), digest=lambda x: 'pin')


def test_combined_failure_and_changed_population():
    rows, activities, operations, schemas, cases = inputs()
    choices = {'a': {'id': '/id', 'key': '/key'}}
    for fail_index in (0, 1):
        def request(name, mapping, data):
            if data['id'] == fail_index:
                raise ValueError('PRIVATE')
        with pytest.raises(ValueError, match='Combined HTTP request rejected') as err:
            m.assemble(rows, choices, activities, operations, schemas, cases, request=request, digest=lambda x: 'pin')
        assert 'PRIVATE' not in str(err.value)
    with pytest.raises(ValueError, match='population'):
        m.assemble(rows, choices, activities, operations, schemas, cases[:1], request=lambda *a: None, digest=lambda x: 'pin')


def test_no_argument_activity_still_requires_case_coverage():
    rows = [{'activity_id': 'a', 'operation_name': 'read', 'relevant_cases': 0, 'arguments': []}]
    with pytest.raises(ValueError, match='population'):
        m.assemble(rows, {'a': {}}, {'a': {'implementation': 'api'}}, {'read': {'method': 'GET', 'fields': {}}},
                   {'read': {}}, [], request=lambda *a: None, digest=lambda x: 'pin')
