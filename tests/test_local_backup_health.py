"""M7/R20: local backup health checks never restore, follow symlinks or expose secrets."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from server import local_backup
from server import local_backup_health as health

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data=tmp_path/"data"
    auth=data/"auth"
    auth.mkdir(parents=True)
    (data/"source.txt").write_text("SYNTHETIC_PRIVATE_WORK_TEXT", encoding="utf-8")
    (auth/".api_token").write_text("SYNTHETIC_SECRET_TOKEN", encoding="utf-8")
    folder=tmp_path/"backups"
    folder.mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    monkeypatch.setenv("OWG_BACKUP_DIR", str(folder))
    return tmp_path,data,auth,folder


def _request(*names):
    return {"names": list(names), "confirm_local_verification": True}


def _write_backup(folder: Path, name: str):
    path=folder/name
    local_backup.create_backup(path)
    return path


def _as_names(rows):
    return {r["name"]: r for r in rows}


def test_explicit_integrity_check_is_bounded_read_only_and_excludes_credentials(store):
    _root,data,auth,folder=store
    good=_write_backup(folder,"owg-backup-good.zip")
    original=good.read_bytes()
    source=(data/"source.txt").read_bytes()
    result=health.check(_request(good.name))
    assert result["format"]=="openworkgraph.local-backup-health.v1"
    assert result["requested"]==1 and result["verified"]==1
    assert result["not_verified"]==0 and result["all_selected_verified"]
    assert result["max_archives_per_check"]==5
    assert result["archive_contents_returned"] is False
    assert result["restores_performed"]==0 and result["offline_restore_authorized"] is False
    assert result["local_only"]
    item=result["items"][0]
    assert item["verified"] and item["status"]=="verified"
    assert item["file_count"]==1
    assert item["archive_bytes"]==len(original)
    assert item["owg_version"]
    assert good.read_bytes()==original
    assert (data/"source.txt").read_bytes()==source
    assert b"SYNTHETIC_SECRET_TOKEN" not in original
    assert not list(folder.glob("*.pre-restore-*"))
    assert not list(_root.glob("data.pre-restore-*"))
    assert "SYNTHETIC_PRIVATE_WORK_TEXT" not in str(result)
    assert str(_root) not in str(result)


def test_tampered_plaintext_zip_is_not_accepted_and_does_not_change_files(store):
    _root,data,auth,folder=store
    original=_write_backup(folder,"owg-backup-valid.zip")
    damaged=folder/"owg-backup-damaged.zip"
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(damaged,"w",zipfile.ZIP_DEFLATED) as dst:
        for entry in src.infolist():
            body=src.read(entry.filename)
            if entry.filename=="data/source.txt":
                body=b"CHANGED_PRIVATE_WORK_TEXT"
            dst.writestr(entry.filename,body)
    old=damaged.read_bytes()
    checked=health.check(_request(original.name,damaged.name))
    rows=_as_names(checked["items"])
    assert rows[original.name]["verified"] is True
    assert rows[damaged.name]["verified"] is False
    assert rows[damaged.name]["status"]=="integrity_check_failed"
    assert checked["verified"]==1 and checked["not_verified"]==1
    assert not checked["all_selected_verified"]
    assert damaged.read_bytes()==old
    assert (data/"source.txt").read_text()=="SYNTHETIC_PRIVATE_WORK_TEXT"
    assert "CHANGED_PRIVATE_WORK_TEXT" not in str(checked)
    assert "source.txt" not in str(checked)


def test_encrypted_backup_is_offline_only_and_never_prompts_for_passphrase(store):
    root,data,auth,folder=store
    path=folder/"owg-backup-encrypted.zip"
    local_backup.create_backup(path,passphrase="synthetic test passphrase long enough")
    checked=health.check(_request(path.name))
    assert checked["items"][0]["status"]=="encrypted_requires_offline_cli"
    assert checked["items"][0]["verified"] is False
    assert "passphrase" not in str(checked).lower()
    assert checked["verified"]==0 and checked["not_verified"]==1


def test_listed_symlink_refused_without_reading_target(store):
    root,data,auth,folder=store
    outside=root/"do-not-touch.zip"
    outside.write_bytes(b"THIS IS NOT A BACKUP")
    link=folder/"owg-backup-link.zip"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("the test OS does not permit symlinks")
    report=health.check(_request(link.name))
    assert report["items"][0]["status"]=="unavailable_or_not_regular"
    assert not report["items"][0]["verified"]
    assert outside.read_bytes()==b"THIS IS NOT A BACKUP"
    assert str(outside) not in str(report)


def test_oversize_archive_disclosed_not_loaded(store):
    root,data,auth,folder=store
    giant=folder/"owg-backup-oversize.zip"
    with giant.open("wb") as handle:
        handle.truncate(health.MAX_ARCHIVE_BYTES+1)
    report=health.check(_request(giant.name))
    assert report["items"][0]["status"]=="exceeds_dashboard_archive_limit"
    assert not report["items"][0]["verified"]
    assert report["archive_bytes_read_budgeted"]==0
    assert giant.stat().st_size==health.MAX_ARCHIVE_BYTES+1


@pytest.mark.parametrize("payload", [
    {}, [], None, {"names":[],"confirm_local_verification":True},
    {"names":["owg-backup-a.zip"]}, {"names":["owg-backup-a.zip"],"confirm_local_verification":"true"},
    {"names":["owg-backup-a.zip"],"confirm_local_verification":True,"passphrase":"secret"},
    {"names":["../owg-backup-a.zip"],"confirm_local_verification":True},
    {"names":["owg-backup-a.zip","owg-backup-a.zip"],"confirm_local_verification":True},
    {"names":["owg-backup-a.zip"]*6,"confirm_local_verification":True},
    {"names":[42],"confirm_local_verification":True},
])
def test_malformed_or_nonconsensual_verification_request_refused(store,payload):
    with pytest.raises(health.BackupHealthError):
        health.check(payload)


def test_unlisted_archive_name_does_not_probe_other_files(store):
    root,data,auth,folder=store
    _write_backup(folder,"owg-backup-actual.zip")
    with pytest.raises(health.BackupHealthError,match="no longer in the local list"):
        health.check(_request("owg-backup-not-listed.zip"))


def test_listed_file_changing_during_verification_cannot_be_reported_intact(store,monkeypatch):
    root,data,auth,folder=store
    good=_write_backup(folder,"owg-backup-race.zip")
    original=local_backup.verify_backup
    def replace_during_read(path):
        result=original(path)
        # Replacing or changing a backup during an integrity check invalidates
        # the post-scan exact-file claim, even if earlier bytes were valid.
        path.write_bytes(path.read_bytes()+b"late-synthetic-appended-byte")
        return result
    monkeypatch.setattr(local_backup,"verify_backup",replace_during_read)
    report=health.check(_request(good.name))
    assert report["items"][0]["status"]=="changed_during_check"
    assert report["items"][0]["verified"] is False
    assert report["items"][0]["owg_version"] is None


def test_owner_http_check_and_bearer_denial_on_the_actual_dashboard(tmp_path):
    code=r'''
from pathlib import Path
from fastapi.testclient import TestClient
from server.secure_app import app
import server.basic_mode_routes
from server import local_auth, local_backup
folder=Path(__import__("os").environ["OWG_BACKUP_DIR"])
folder.mkdir(parents=True,exist_ok=True)
data=Path(__import__("os").environ["WORKFLOW_OBSERVER_DATA"])
data.mkdir(parents=True,exist_ok=True)
(data/"source.txt").write_text("PRIVATE",encoding="utf8")
name="owg-backup-health-check.zip"
local_backup.create_backup(folder/name)
with TestClient(app) as client:
    url="/v1/local-backups/health"
    body={"names":[name],"confirm_local_verification":True}
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session(),
           "Content-Type":"application/json"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token(),
            "Content-Type":"application/json"}
    assert client.post(url,json=body).status_code==403
    assert client.post(url,json=body,headers=bearer).status_code==403
    bad=client.post(url,json={"names":[name]},headers=owner)
    assert bad.status_code==422
    valid=client.post(url,json=body,headers=owner)
    assert valid.status_code==200,valid.text
    assert valid.headers["cache-control"]=="no-store"
    assert valid.json()["verified"]==1
    assert valid.json()["items"][0]["status"]=="verified"
    assert str(folder) not in valid.text
    assert client.post(url,data="{}"*1000,headers=owner).status_code==413
    assert client.post(url,data="{}",headers={"Authorization":owner["Authorization"],
                                             "Content-Type":"text/plain"}).status_code==415
'''
    env=os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
                "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
                "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json"),
                "OWG_BACKUP_DIR":str(tmp_path/"backups")})
    run=subprocess.run([sys.executable,"-c",code],cwd=ROOT,env=env,
                       capture_output=True,text=True,timeout=120)
    assert run.returncode==0,f"stdout={run.stdout}\nstderr={run.stderr}"


def test_privacy_ui_only_wires_readonly_bounded_owner_verification():
    source=(ROOT/"dashboard/basic_mode.js").read_text(encoding="utf-8")
    routes=(ROOT/"server/basic_mode_routes.py").read_text(encoding="utf-8")
    assert 'id="pvBackupHealthRun"' in source
    assert 'id="pvBackupHealthStatus"' in source
    assert 'id="pvBackupHealthRows"' in source
    assert "listedBackups.slice(0, 5)" in source
    assert "confirm_local_verification: true" in source
    assert "api('/v1/local-backups/health'" in source
    assert "row.textContent = item.name" in source
    assert 'app.add_api_route("/v1/local-backups/health"' in routes
    assert "_require_dashboard_session(request)" in routes.split("async def post_local_backup_health(")[1].split(
        "def _require_dashboard_session(",1)[0]



def test_large_declared_extracted_payload_is_not_decompressed_in_dashboard(store, monkeypatch):
    _root, _data, _auth, folder = store
    path = _write_backup(folder, "owg-backup-high-expansion.zip")
    monkeypatch.setattr(health, "_declared_uncompressed_size",
                        lambda candidate: health.MAX_UNCOMPRESSED_BYTES + 1)
    def forbidden(_path):
        raise AssertionError("oversize ZIP must not reach full verification")
    monkeypatch.setattr(local_backup, "verify_backup", forbidden)
    result = health.check(_request(path.name))
    assert result["items"][0]["status"] == "exceeds_dashboard_uncompressed_limit"
    assert not result["items"][0]["verified"]
    assert result["max_uncompressed_archive_bytes"] == 2 << 30
