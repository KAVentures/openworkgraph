"""Owned local paired export and synthetic delegated Gateway identities."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from server import event_log_workspace as workspace
from server.analysis_case_export import prepare
from test_shared_analysis_publications import app as factory
from gateway.analysis_case_data import READ,WRITE
scratch=Path(os.environ['OWG_BROWSER_TEST_DIR'])
source=os.environ.get('OWG_SHARED_CASE_PAIR_INPUT')
if source:pair=json.loads(Path(source).read_text())
else:
    workspace.DATA_DIR=scratch/'local';workspace.DATA_DIR.mkdir()
    log=workspace.import_log(BytesIO(b'case,activity,time\nprivate,A,2024-01-01T00:00:00Z\nprivate,B,2024-01-01T00:01:00Z\nother,A,2024-01-02T00:00:00Z\n'),name='private',source_format='csv',mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']
    pair=prepare(log,{'level':'cases','dimensions':[],'measures':[{'fn':'count'}]})
(scratch/'aggregate.json').write_text(json.dumps(pair['aggregate_snapshot']))
(scratch/'cases.json').write_text(json.dumps(pair['case_dataset']))
app=factory(scratch);app.state.human_access_settings.group_scope_map['analysis']|={READ,WRITE}
