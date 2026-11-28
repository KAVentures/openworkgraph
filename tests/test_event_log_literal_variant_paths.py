"""Distinct literal activity sequences remain distinct throughout case analyses."""
from __future__ import annotations

import csv
import json
from io import BytesIO, StringIO

import pytest

from server import event_log_activity_mappings as mappings
from server import event_log_analytics as analytics
from server import event_log_insights as insights
from server import event_log_variant_export as variants_export
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def test_mapped_separator_label_does_not_merge_with_a_multi_activity_path():
    rows = StringIO()
    writer = csv.writer(rows)
    writer.writerow(['case', 'activity', 'time'])
    for i in range(12):
        steps = ['Raw'] if i < 6 else ['A', 'B']
        for step, activity in enumerate(steps):
            writer.writerow([f'c{i:02}', activity, f'2024-03-01T0{step}:00:00Z'])
    log = workspace.import_log(BytesIO(rows.getvalue().encode()), name='literal paths', source_format='csv',
        mapping={'case_column':'case', 'activity_column':'activity', 'timestamp_column':'time'})['log_id']
    literal = 'A\x1fB'
    created = mappings.create(log, {'name':'Legacy mapping', 'groups':[{'label':literal, 'activities':['Raw']}]})
    mappings.apply_mapping(log, created['mapping_id'])

    variants = workspace.variants(log)
    assert variants['distinct_variants'] == 2
    assert {tuple(v['path']):v['cases'] for v in variants['variants']} == {(literal,):6, ('A','B'):6}
    assert analytics.kpis(log, cohort={'variants':[[literal]]})['cases'] == 6
    assert analytics.kpis(log, cohort={'variants':[['A','B']]})['cases'] == 6
    assert analytics.kpis(log, cohort={'path_cases':{'min':7}})['cases'] == 0
    spread = next(item for item in insights.insights(log, {'limit':30})['findings'] if item['kind']=='variant_spread')
    assert spread['numbers']['distinct_variants'] == 2
    outcome = analytics.root_cause(log, 'variant_in', variants=[[literal]])
    assert outcome['cases'] == 12 and outcome['outcome_cases'] == 6
    model = {'format':'openworkgraph.reference-model.v1', 'paths':[[literal]]}
    conformance = analytics.conformance(log, model)
    assert conformance['variants_observed'] == 2 and conformance['fitting_cases'] == 6
    exported, meta = variants_export.variants_csv(log)
    exported_rows = list(csv.DictReader(StringIO(exported)))
    assert meta['variants'] == 2
    exact, exact_meta = variants_export.variants_csv(log, exact_paths=True)
    exact_rows = list(csv.DictReader(StringIO(exact)))
    assert exact_meta == meta
    assert {tuple(json.loads(row['path_json'])) for row in exact_rows} == {(literal,), ('A','B')}
    assert {(row['path'], int(row['steps']), int(row['cases'])) for row in exported_rows} == {
        (literal, 1, 6), ('A > B', 2, 6)}


def test_exact_csv_disambiguates_identical_human_readable_paths():
    rows = StringIO()
    writer = csv.writer(rows)
    writer.writerow(['case', 'activity', 'time'])
    for case, steps in [('one',['Raw']), ('two',['A','B'])]:
        for i, activity in enumerate(steps):
            writer.writerow([case, activity, f'2024-03-01T0{i}:00:00Z'])
    log = workspace.import_log(BytesIO(rows.getvalue().encode()), name='paths', source_format='csv',
        mapping={'case_column':'case', 'activity_column':'activity', 'timestamp_column':'time'})['log_id']
    made = mappings.create(log, {'name':'Combined', 'groups':[{'label':'A > B', 'activities':['Raw']}]})
    mappings.apply_mapping(log, made['mapping_id'])
    text, meta = variants_export.variants_csv(log, exact_paths=True)
    result = list(csv.DictReader(StringIO(text)))
    assert meta['variants'] == 2 and {row['path'] for row in result} == {'A > B'}
    assert {tuple(json.loads(row['path_json'])) for row in result} == {('A > B',), ('A','B')}
