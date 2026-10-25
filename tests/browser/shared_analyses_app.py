"""Loopback-only synthetic identities; production local export and Gateway routes."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_shared_analysis_publications import app as make_app
from server import event_log_workspace as workspace
from server.analysis_snapshot_export import export_snapshot
from shared.analysis_snapshot import Snapshot

scratch=Path(os.environ['OWG_BROWSER_TEST_DIR'])
workspace.DATA_DIR=scratch/'local';workspace.DATA_DIR.mkdir()
source=os.environ.get('OWG_ANALYSIS_SNAPSHOT_INPUT')
if source:
    packet=Snapshot.model_validate_json(Path(source).read_text()).model_dump(mode='json')
else:
    doc=b'case,activity,time,resource\nprivate-case,A,2024-01-01T00:00:00Z,private-person\nprivate-case,B,2024-01-01T01:00:00Z,private-person\nother-case,A,2024-01-02T00:00:00Z,other-person\n'
    log=workspace.import_log(BytesIO(doc),name='private-log',source_format='csv',mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'resource'})['log_id']
    packet=export_snapshot(log,{'level':'cases','dimensions':['last_activity'],'measures':[{'fn':'count'}]})
(scratch/'snapshot.json').write_text(json.dumps(packet))
app=make_app(scratch)
