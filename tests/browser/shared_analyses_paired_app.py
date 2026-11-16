"""Real local-to-company paired sharing fixture using the production endpoints."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))

from test_shared_analysis_publications import app as make_app
from server import event_log_workspace as workspace
from server.analysis_case_export import prepare
from gateway.analysis_case_data import READ, WRITE

scratch=Path(os.environ["OWG_BROWSER_TEST_DIR"])
workspace.DATA_DIR=scratch/"local"
workspace.DATA_DIR.mkdir()
csv=(
    b"case,activity,time,resource\n"
    b"private-001,Open,2026-01-01T00:00:00Z,private-person\n"
    b"private-001,Review,2026-01-01T01:00:00Z,private-person\n"
    b"private-002,Open,2026-01-02T00:00:00Z,other-person\n"
    b"private-002,Finish,2026-01-02T04:00:00Z,other-person\n"
    b"private-003,Open,2026-01-03T00:00:00Z,third-person\n"
    b"private-003,Finish,2026-01-03T09:00:00Z,third-person\n"
)
log=workspace.import_log(BytesIO(csv),name="Private sample",source_format="csv",
                         mapping={"case_column":"case","activity_column":"activity",
                                  "timestamp_column":"time","resource_column":"resource"})["log_id"]
paired=prepare(log,{"level":"cases","dimensions":[],"measures":[{"fn":"count"}]})
(scratch/"paired-snapshot.json").write_text(json.dumps(paired["aggregate_snapshot"]))
(scratch/"paired-cases.json").write_text(json.dumps(paired["case_dataset"]))
app=make_app(scratch)
app.state.human_access_settings.group_scope_map["analysis"]|={READ,WRITE}
