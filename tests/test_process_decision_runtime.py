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
            assert archive.read('owg_model/'+name)==(Path(__file__).parents[1]/'shared'/name).read_bytes()
        archive.extractall(tmp_path)
    result=subprocess.run([os.environ.get('OWG_AGENT_SDK_PYTHON',sys.executable),'-m','unittest','discover','-v'],
        cwd=tmp_path,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'test_matching_route_rechecks' in result.stderr
    if os.environ.get('OWG_AGENT_SDK_PYTHON'):
        assert 'test_matching_source_supports_only_a_nonexecuting_proposal' in result.stderr
