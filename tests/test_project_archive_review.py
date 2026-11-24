"""Exact tracked project archive reconciliation (R17)."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from server import automation_pipeline as pipeline, opportunity_projects as projects
from server import project_archive_review as review
from test_process_opportunity_lineage import catalog, save, ref, assess


@pytest.fixture
def exported(catalog):
    record = save(catalog)
    idea = assess(pipeline.create_from_process(ref(record)))
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    content, project = projects.export_project(idea["idea_id"], {"expected_revision": idea["revision"]})
    return idea, content, project


def test_original_archive_matches_entire_inventory_without_mutation(exported):
    idea, content, project = exported
    before = projects.projects(idea["idea_id"])
    result = review.review_archive(idea["idea_id"], content)
    assert result["exact_retained_archive_bytes_match"]
    assert result["project_id"] == project["project_id"]
    assert result["archive_sha256"] == project["archive_sha256"]
    assert result["spec_sha256"] == project["spec_sha256"]
    assert result["idea_revision_matches_export"]
    assert result["process_reference_status"] == "current"
    assert result["generated_file_inventory_status"] == "complete_generated_file_inventory_matches"
    with ZipFile(BytesIO(content)) as archive:
        assert result["verified_generated_file_count"] == len(archive.namelist()) - 2
    for key in ("archive_extracted", "archive_persisted", "generated_code_executed",
                "independent_signature_verified", "deployment_verified", "business_outcomes_verified",
                "execution_authorized"):
        assert result[key] is False
    assert projects.projects(idea["idea_id"]) == before


def test_modified_cross_idea_and_oversized_archives_refused(exported, monkeypatch):
    idea, content, _ = exported
    iid = idea["idea_id"]
    # The checksum check precedes ZIP parsing.
    def forbidden(*args):
        pytest.fail("unmatched bytes must never be parsed")
    monkeypatch.setattr(review, "ZipFile", forbidden)
    for invalid in (content+b" ", b"not a zip"):
        with pytest.raises(pipeline.PipelineError, match="does not match"):
            review.review_archive(iid, invalid)
    other = pipeline.create({"title": "Other idea"})
    with pytest.raises(pipeline.PipelineError, match="does not match"):
        review.review_archive(other["idea_id"], content)
    monkeypatch.setattr(review, "MAX_ARCHIVE_BYTES", len(content)-1)
    with pytest.raises(pipeline.PipelineError, match="16 MiB"):
        review.review_archive(iid, content)


def test_historical_export_not_confused_with_current_design(catalog, exported):
    idea, content, _ = exported
    iid = idea["idea_id"]
    pipeline.transition(iid, {"to": "on_hold", "expected_revision": idea["revision"]})
    save(catalog, 1, "Changed process")
    result = review.review_archive(iid, content)
    assert result["exact_retained_archive_bytes_match"]
    assert not result["idea_revision_matches_export"]
    assert result["process_reference_status"] == "changed"


def test_inner_inventory_detects_changed_members_even_with_outer_digest_replaced(exported):
    idea, content, project = exported
    buffer = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(buffer, "w") as destination:
        for entry in source.infolist():
            raw = source.read(entry)
            destination.writestr(entry, raw + b"\n# changed" if entry.filename == "agent.py" else raw)
    modified = buffer.getvalue()
    from hashlib import sha256
    with pipeline._connect() as conn:
        conn.execute("UPDATE idea_projects SET archive_sha256=? WHERE project_id=?",
                     (sha256(modified).hexdigest(), project["project_id"]))
    with pytest.raises(pipeline.PipelineError, match="inventory"):
        review.review_archive(idea["idea_id"], modified)


def test_real_archive_review_route(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, automation_pipeline as p, opportunity_projects as projects
from server.local_process_catalog import catalog
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_process_opportunity_lineage import save, ref, assess
i=assess(p.create_from_process(ref(save(catalog))))
i=p.transition(i['idea_id'],{'to':'approved','expected_revision':i['revision']})
content,project=projects.export_project(i['idea_id'],{'expected_revision':i['revision']})
url='/v1/automation-pipeline/'+i['idea_id']+'/project-archive-review'
with TestClient(app) as c:
    h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session(),'Content-Type':'application/zip'}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token(),'Content-Type':'application/zip'}
    assert c.post(url,content=content,headers=bearer).status_code in (401,403)
    result=c.post(url,content=content,headers=h)
    assert result.status_code==200,result.text
    assert result.headers['cache-control']=='no-store'
    assert result.json()['archive_sha256']==project['archive_sha256']
    assert result.json()['generated_code_executed'] is False
    assert c.post(url,content=content+b' ',headers=h).status_code==422
    assert c.post(url,content=content,headers={**h,'Content-Type':'application/json'}).status_code==415
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/"data"),
               WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/"auth"),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path/"config.json"))
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout+result.stderr
