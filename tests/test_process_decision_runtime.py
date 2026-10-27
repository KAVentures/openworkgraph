"""Portable decision enforcement runs with installed dependencies, no model provider."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest
from shared.process_agent_project import build_runtime_project
from test_process_decision_tables import record, graph


@pytest.mark.parametrize('parallel',[False,True])
def test_exported_decision_project_runs_real_offline_sdk_source_and_execution_oracles(tmp_path,parallel):
    raw=graph()
    if parallel:
        raw['activities'] += [{'id':'fork','label':'Both','kind':'parallel_split'},
                              {'id':'join','label':'Complete','kind':'parallel_join'},
                              {'id':'done','label':'Done','kind':'end'}]
        raw['transitions'] += [{'source':a,'target':b} for a,b in [('fork','accept'),('fork','join'),('accept','join'),('join','done')]]
        raw['transitions'][0]['target']='fork'
        raw['decision_tables'][0]['rows'][0]['target_activity_id']='fork'
        raw['terminal_activity_ids']=['done','escalate']
    payload=record(raw)
    with ZipFile(BytesIO(build_runtime_project(payload))) as archive:
        spec=json.loads(archive.read('workflow.spec.json'))
        assert spec['decision_table_contract']=='openworkgraph.current-source-unique-decision.v1'
        assert not spec['execution_authorization']
        for name in ('process_model.py','process_decision_tables.py','process_rule_conditions.py'):
            assert archive.read('owg_model/'+name)==(Path(__file__).parents[1]/'shared'/name).read_text(encoding='utf-8').encode('utf-8')
        archive.extractall(tmp_path)
    result=subprocess.run([os.environ.get('OWG_AGENT_SDK_PYTHON',sys.executable),'-m','unittest','discover','-v'],
        cwd=tmp_path,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'test_matching_route_rechecks' in result.stderr
    if os.environ.get('OWG_AGENT_SDK_PYTHON'):
        assert 'test_matching_source_supports_only_a_nonexecuting_proposal' in result.stderr


def test_crlf_source_checkout_produces_identical_portable_project(tmp_path,monkeypatch):
    import shared.process_agent_project as project
    payload=record()
    expected=project.build_runtime_project(payload)
    source=tmp_path/'shared';source.mkdir()
    original=Path(project.__file__).parent
    names=('process_source_state.py','process_rule_conditions.py','process_model.py',
           'process_parallel.py','process_opportunities.py','process_decision_tables.py')
    for name in names:
        data=(original/name).read_text(encoding='utf-8').replace('\n','\r\n').encode('utf-8')
        assert b'\r\n' in data
        (source/name).write_bytes(data)
    templates=source/'process_agent_templates';templates.mkdir()
    for template in project._TEMPLATES.glob('*.txt'):
        (templates/template.name).write_bytes(template.read_text(encoding='utf-8').replace('\n','\r\n').encode('utf-8'))
    monkeypatch.setattr(project,'__file__',str(source/'process_agent_project.py'))
    monkeypatch.setattr(project,'_TEMPLATES',templates)
    assert project.build_runtime_project(payload)==expected
