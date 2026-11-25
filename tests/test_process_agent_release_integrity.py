"""Customer runtime release bytes and offline verifier (R17).

These synthetic tests are intentionally self-contained: no real provider,
customer credentials, agent tool calls or model inference are used.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from shared.process_agent_project import build_runtime_project
from shared.process_agent_release import ReleaseIntegrityError, verify_directory, compare_directories, create_manifest
from shared.process_model import ProcessModel


def _packet():
    model = ProcessModel(
        goal="Review synthetic approval",
        baseline_source="user_declared",
        baseline_path=["Receive", "Approve", "Close"],
        activities=[{"id": "receive", "label": "Receive"},
                    {"id": "approve", "label": "Approve", "kind": "end"}],
        transitions=[{"source": "receive", "target": "approve"}],
        entry_activity_id="receive", terminal_activity_ids=["approve"],
    )
    return {"format": "openworkgraph.versioned-process-export.v1",
            "workflow_id": "synthetic_release", "revision": 3,
            "content_sha256": "b" * 64, "process_model": model.model_dump(),
            "execution_authorization": False}


def _extract(root: Path):
    archive = build_runtime_project(_packet())
    assert archive == build_runtime_project(_packet()), "exported ZIP must be deterministic"
    with ZipFile(BytesIO(archive)) as z:
        names = z.namelist()
        assert names.count("release-manifest.json") == 1
        assert names.count("verify_release.py") == 1
        assert len(names) == len(set(names))
        z.extractall(root)
    return archive


def test_export_includes_offline_manifest_verifier_and_exact_workflow_identity(tmp_path):
    _extract(tmp_path)
    digest = hashlib.sha256((tmp_path / "release-manifest.json").read_bytes()).hexdigest()
    result = verify_directory(tmp_path, expected_manifest_sha256=digest)
    assert result["generated_file_integrity_ok"] is True
    assert result["manifest_sha256"] == digest
    assert result["workflow_reference"] == {
        "workflow_id": "synthetic_release", "revision": 3, "content_sha256": "b" * 64}
    assert result["verified_generated_file_count"] == 34
    assert result["independently_signed"] is False
    assert result["remote_deployment_verified"] is False
    assert result["remote_execution_or_outcome_verified"] is False
    manifest = json.loads((tmp_path / "release-manifest.json").read_text())
    assert "release-manifest.json" not in manifest["files"]
    assert manifest["files"]["workflow.spec.json"] == hashlib.sha256(
        (tmp_path / "workflow.spec.json").read_bytes()).hexdigest()
    assert manifest["files"]["verify_release.py"] == hashlib.sha256(
        (tmp_path / "verify_release.py").read_bytes()).hexdigest()
    # Standard library only; --manifest-sha256 pins externally observed bytes.
    run = subprocess.run([sys.executable, "-S", "verify_release.py",
                          "--root", ".", "--manifest-sha256", digest],
                         cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert "No independent signature, remote deployment or business outcome" in run.stdout
    assert digest in run.stdout
    assert "verify_release.py --root ." in (tmp_path / "README.md").read_text()


@pytest.mark.parametrize("filename", ["agent.py", "workflow.spec.json", "verify_release.py"])
def test_modified_generated_file_is_refused_even_if_the_rest_is_intact(tmp_path, filename):
    _extract(tmp_path)
    target = tmp_path / filename
    target.write_bytes(target.read_bytes() + b"\n# corrupted")
    with pytest.raises(ReleaseIntegrityError, match="missing or changed"):
        verify_directory(tmp_path)
    run = subprocess.run([sys.executable, "-S", "verify_release.py", "--root", "."],
                         cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert run.returncode == 2
    assert "FAILED" in run.stderr


def test_missing_file_replacement_and_malformed_manifest_are_refused(tmp_path):
    _extract(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    with pytest.raises(ReleaseIntegrityError, match="missing or changed"):
        verify_directory(tmp_path)
    _extract(tmp_path)
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(manifest.read_text().replace('"format": ', '"format": "fake", "format": ', 1))
    with pytest.raises(ReleaseIntegrityError, match="Duplicate field"):
        verify_directory(tmp_path)


def test_unsigned_manifest_cannot_omit_required_generated_runtime_files(tmp_path):
    _extract(tmp_path)
    path = tmp_path / "release-manifest.json"
    data = json.loads(path.read_text())
    data["files"].pop("execute.py")
    path.write_text(json.dumps(data))
    with pytest.raises(ReleaseIntegrityError, match="Invalid release file inventory"):
        verify_directory(tmp_path)


def test_bounded_verification_refuses_oversized_manifest_and_extracted_files(tmp_path):
    _extract(tmp_path)
    (tmp_path / "release-manifest.json").write_bytes(b" " * (256 * 1024 + 1))
    with pytest.raises(ReleaseIntegrityError, match="manifest is too large"):
        verify_directory(tmp_path)
    _extract(tmp_path)
    (tmp_path / "agent.py").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(ReleaseIntegrityError, match="missing or changed"):
        verify_directory(tmp_path)


def test_manifest_cannot_follow_parent_paths_or_untracked_symlinks(tmp_path):
    _extract(tmp_path)
    manifest = tmp_path / "release-manifest.json"
    data = json.loads(manifest.read_text())
    data["files"]["../private.txt"] = "a" * 64
    manifest.write_text(json.dumps(data))
    with pytest.raises(ReleaseIntegrityError, match="unsafe inventory"):
        verify_directory(tmp_path)
    _extract(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside-verifier")
    outside.write_bytes((tmp_path / "agent.py").read_bytes())
    (tmp_path / "agent.py").unlink()
    try:
        (tmp_path / "agent.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This operating system cannot create a file symlink")
    with pytest.raises(ReleaseIntegrityError, match="symbolic link"):
        verify_directory(tmp_path)


def test_external_manifest_digest_rejects_forged_unsigned_self_consistent_archive(tmp_path):
    _extract(tmp_path)
    original = hashlib.sha256((tmp_path / "release-manifest.json").read_bytes()).hexdigest()
    agent = tmp_path / "agent.py"
    agent.write_bytes(agent.read_bytes() + b"\n# attacker edited the runner")
    manifest = tmp_path / "release-manifest.json"
    changed = json.loads(manifest.read_text())
    changed["files"]["agent.py"] = hashlib.sha256(agent.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(changed))
    # This documents the fundamental limit: an unsigned mutable manifest
    # cannot establish origin when both code and manifest are replaced.
    assert verify_directory(tmp_path)["generated_file_integrity_ok"] is True
    with pytest.raises(ReleaseIntegrityError, match="independently pinned digest"):
        verify_directory(tmp_path, expected_manifest_sha256=original)
    # Additional customer-managed files are not claimed to be covered.
    (tmp_path / "customer-bindings.json").write_text('{"enabled":false}')
    assert verify_directory(tmp_path)["generated_file_integrity_ok"] is True


def test_release_verification_does_not_claim_authorized_runtime(tmp_path):
    _extract(tmp_path)
    manifest = json.loads((tmp_path / "release-manifest.json").read_text())
    assert manifest["independent_signature_present"] is False
    assert manifest["generated_files_verified_as_remote_deployment"] is False
    assert manifest["remote_business_outcomes_verified"] is False
    assert json.loads((tmp_path / "workflow.spec.json").read_text())["execution_authorization"] is False


def _pin(root):
    return hashlib.sha256((root / "release-manifest.json").read_bytes()).hexdigest()


def _reinventory(root, mutate=None):
    path = root / "workflow.spec.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    if mutate:
        mutate(spec)
        path.write_text(json.dumps(spec, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    files = {str(p.relative_to(root)).replace(os.sep, "/"): p.read_bytes().decode("utf-8")
             for p in root.rglob("*") if p.is_file() and p.name != "release-manifest.json"}
    (root / "release-manifest.json").write_bytes(create_manifest(files, spec).encode("utf-8"))
    return _pin(root)


def test_compare_exact_release_file_deltas_and_distinct_design_identity(tmp_path):
    old, new = tmp_path/"old", tmp_path/"new"
    _extract(old)
    _extract(new)
    old_pin = _pin(old)
    original = compare_directories(old, new, old_pin, _pin(new))
    assert not original["requires_review_before_upgrade"]
    assert original["revision_direction"] == "same_revision_same_content"
    assert original["generated_file_changes"]["unchanged_count"] == verify_directory(old)["verified_generated_file_count"]
    # Simulate customer-reviewed candidate generator output, retaining the
    # same saved design. The comparison must not mistake code drift for a
    # new model revision, or silently cover external configuration.
    (new/"agent.py").write_bytes((new/"agent.py").read_bytes()+b"\n# candidate")
    (new/"AGENTS.md").write_bytes((new/"AGENTS.md").read_bytes()+b"\nCandidate notes")
    (new/"extra.txt").write_text("new generated file", encoding="utf-8")
    (new/"LICENSE").unlink()
    new_pin = _reinventory(new)
    result = compare_directories(old, new, old_pin, new_pin)
    changes = result["generated_file_changes"]
    assert [x["path"] for x in changes["added"]] == ["extra.txt"]
    assert [x["path"] for x in changes["removed"]] == ["LICENSE"]
    assert [x["path"] for x in changes["changed"]] == ["AGENTS.md", "agent.py"]
    assert not result["specification_bytes_changed"] and not result["process_model_changed"]
    assert result["requires_review_before_upgrade"]
    assert result["both_generated_inventories_verified_against_pins"]
    for claim in ("dependency_compatibility_verified", "runtime_behavior_compatibility_verified",
                  "execution_authorized", "independent_signature_verified",
                  "customer_managed_files_compared", "remote_deployment_verified", "business_outcomes_verified"):
        assert result[claim] is False
    (new/"customer-bindings.json").write_text("PRIVATE_CUSTOMER_CONFIGURATION", encoding="utf-8")
    assert compare_directories(old, new, old_pin, new_pin) == result
    assert "PRIVATE_CUSTOMER_CONFIGURATION" not in json.dumps(result)


def test_comparison_classifies_revision_upgrade_rollback_and_reused_revision(tmp_path):
    old, new = tmp_path/"old", tmp_path/"new"
    _extract(old)
    _extract(new)
    old_pin = _pin(old)
    def upgrade(spec):
        spec["revision"] += 1
        spec["content_sha256"] = "c"*64
        spec["process_model"]["goal"] = "Changed synthetic goal"
        spec["process_model_sha256"] = hashlib.sha256(
            json.dumps(spec["process_model"], sort_keys=True, allow_nan=False).encode()).hexdigest()
    new_pin = _reinventory(new, upgrade)
    result = compare_directories(old, new, old_pin, new_pin)
    assert result["revision_direction"] == "newer_revision"
    assert result["specification_bytes_changed"] and result["process_model_changed"]
    assert result["design_changes"]["other_changed_model_fields"] == ["goal"]
    assert not result["design_changes"]["model_field_values_returned"]
    assert compare_directories(new, old, new_pin, old_pin)["revision_direction"] == "older_revision"
    reused = _reinventory(new, lambda spec: spec.update(revision=3))
    assert compare_directories(old, new, old_pin, reused)["revision_direction"] == "same_revision_different_content"
    foreign = _reinventory(new, lambda spec: spec.update(workflow_id="foreign"))
    with pytest.raises(ReleaseIntegrityError, match="same saved workflow"):
        compare_directories(old, new, old_pin, foreign)


def test_comparison_requires_both_pins_and_refuses_tampered_candidate(tmp_path):
    old, new = tmp_path/"old", tmp_path/"new"
    _extract(old)
    _extract(new)
    old_pin, new_pin = _pin(old), _pin(new)
    for a,b in ((None,new_pin),(old_pin,None),("bad",new_pin)):
        with pytest.raises(ReleaseIntegrityError, match="Both releases"):
            compare_directories(old,new,a,b)
    (new/"agent.py").write_bytes(b"changed")
    with pytest.raises(ReleaseIntegrityError, match="missing or changed"):
        compare_directories(old,new,old_pin,new_pin)
    forged = _reinventory(new)
    with pytest.raises(ReleaseIntegrityError, match="independently pinned digest"):
        compare_directories(old,new,old_pin,new_pin)
    assert compare_directories(old,new,old_pin,forged)["generated_files_changed"]


def test_generated_standalone_compare_cli_outputs_json_and_requires_pins(tmp_path):
    old, new = tmp_path/"old", tmp_path/"new"
    _extract(old)
    _extract(new)
    command = [sys.executable, "-S", str(old/"verify_release.py"), "--root", str(old),
               "--manifest-sha256", _pin(old), "--compare-root", str(new),
               "--compare-manifest-sha256", _pin(new)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["both_generated_inventories_verified_against_pins"]
    refused = subprocess.run(command[:-2], capture_output=True, text=True, timeout=20)
    assert refused.returncode == 2
    assert not refused.stdout


def test_structural_comparison_exact_ids_order_and_changed_fields(tmp_path):
    old, new = tmp_path/"old", tmp_path/"new"
    _extract(old)
    _extract(new)
    old_pin = _pin(old)
    def change(spec):
        model = spec["process_model"]
        model["activities"][0]["label"] = "PRIVATE_NEW_LABEL"
        model["activities"].append({"id":"escalate","label":"PRIVATE_NEW_ACTIVITY"})
        model["transitions"] = [{"source":"receive","target":"escalate"},
                                {"source":"escalate","target":"approve"}]
        model["business_rules"] = [{"id":"rule1","text":"PRIVATE_RULE"}]
        model["outcome_expectations"] = [{"terminal_activity_id":"approve","operation":"updated"}]
        spec["control_flow_contract"] = "synthetic_candidate_contract"
        spec["process_model_sha256"] = hashlib.sha256(
            json.dumps(model,sort_keys=True,allow_nan=False).encode()).hexdigest()
    pin = _reinventory(new, change)
    report = compare_directories(old,new,old_pin,pin)["design_changes"]
    groups = report["keyed_model_changes"]
    assert groups["activities"]["added"] == [{"id":"escalate"}]
    assert groups["activities"]["changed"] == [{"id":"receive","changed_fields":["label"]}]
    assert groups["transitions"]["removed"] == [{"source":"receive","target":"approve"}]
    assert groups["transitions"]["added"] == [{"source":"escalate","target":"approve"},
                                             {"source":"receive","target":"escalate"}]
    assert groups["business_rules"]["added"] == [{"id":"rule1"}]
    assert groups["outcome_expectations"]["added"] == [{"terminal_activity_id":"approve","operation":"updated"}]
    assert report["changed_runtime_contract_fields"] == ["control_flow_contract"]
    assert "PRIVATE_" not in json.dumps(report)
    assert not report["structural_changes_establish_compatibility"]
    def reorder(spec):
        spec["process_model"]["activities"].reverse()
        spec["process_model_sha256"] = hashlib.sha256(
            json.dumps(spec["process_model"],sort_keys=True,allow_nan=False).encode()).hexdigest()
    reordered = _reinventory(new,reorder)
    assert compare_directories(old,new,old_pin,reordered)["design_changes"]["keyed_model_changes"]["activities"]["retained_identity_order_changed"]


def test_structural_inventory_refuses_duplicate_identity(tmp_path):
    old,new=tmp_path/"old",tmp_path/"new"
    _extract(old)
    _extract(new)
    def duplicate(spec):
        model=spec["process_model"]
        model["activities"].append(dict(model["activities"][0]))
        spec["process_model_sha256"]=hashlib.sha256(json.dumps(model,sort_keys=True,allow_nan=False).encode()).hexdigest()
    with pytest.raises(ReleaseIntegrityError,match="duplicate model inventory identity"):
        compare_directories(old,new,_pin(old),_reinventory(new,duplicate))


def test_structural_comparison_distinguishes_boolean_and_numeric_json_values():
    from shared.process_agent_release import _structural_changes
    before = {"process_model":{"activities":[{"id":"a","value":True}],
                              "extra":1}, "runtime_extra":1}
    after = {"process_model":{"activities":[{"id":"a","value":1}],
                             "extra":1.0}, "runtime_extra":True}
    delta = _structural_changes(before, after)
    assert delta["keyed_model_changes"]["activities"]["changed"] == [
        {"id":"a","changed_fields":["value"]}]
    assert delta["other_changed_model_fields"] == ["extra"]
    assert delta["changed_runtime_contract_fields"] == ["runtime_extra"]


def test_randomized_structural_delta_against_set_oracle():
    import random
    from shared.process_agent_release import _structural_changes
    rng = random.Random(20261124)
    values = [True, 1, 1.0, False, 0, None, "x", {"x":True}, {"x":1}]
    for _ in range(1000):
        a = rng.sample(list("abcdefghijk"), rng.randrange(12))
        b = rng.sample(list("abcdefghijk"), rng.randrange(12))
        av = {key:rng.choice(values) for key in a}
        bv = {key:rng.choice(values) for key in b}
        old = {"process_model":{"activities":[{"id":k,"value":av[k]} for k in a]}}
        new = {"process_model":{"activities":[{"id":k,"value":bv[k]} for k in b]}}
        delta = _structural_changes(old,new)["keyed_model_changes"]["activities"]
        assert {v["id"] for v in delta["added"]} == set(b)-set(a)
        assert {v["id"] for v in delta["removed"]} == set(a)-set(b)
        assert {v["id"] for v in delta["changed"]} == {
            k for k in set(a)&set(b) if json.dumps(av[k],sort_keys=True)!=json.dumps(bv[k],sort_keys=True)}
        assert delta["retained_identity_order_changed"] == (
            [k for k in a if k in b]!=[k for k in b if k in a])


def _archive(root,path,extra=(),omit=()):
    with ZipFile(path,"w") as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                name=p.relative_to(root).as_posix()
                if name not in omit:z.writestr(name,p.read_bytes())
        for name,data in extra:z.writestr(name,data)
    return path


def test_verify_archive_before_extraction_and_cli(tmp_path):
    from shared.process_agent_release import verify_archive
    root=tmp_path/"generated"
    data=_extract(root)
    path=tmp_path/"release.zip";path.write_bytes(data)
    pin=_pin(root)
    result=verify_archive(path,pin)
    assert result["generated_file_integrity_ok"] and result["archive_inventory_closed"]
    assert result["archive_sha256"]==hashlib.sha256(data).hexdigest()
    assert not result["archive_extracted"] and not result["execution_authorized"]
    before=set(tmp_path.iterdir())
    p=subprocess.run([sys.executable,"-S",str(root/"verify_release.py"),"--archive",str(path),
                      "--manifest-sha256",pin],capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)==result
    assert set(tmp_path.iterdir())==before
    for bad in (None,"bad","f"*64):
        with pytest.raises(ReleaseIntegrityError):verify_archive(path,bad)


def test_archive_upgrade_compares_exact_content_not_zip_container(tmp_path):
    from shared.process_agent_release import compare_archives
    a,b=tmp_path/"a",tmp_path/"b"
    _extract(a);_extract(b)
    old=_archive(a,tmp_path/"a.zip")
    new=_archive(b,tmp_path/"b.zip")
    result=compare_archives(old,new,_pin(a),_pin(b))
    assert not result["generated_files_changed"] and not result["requires_review_before_upgrade"]
    (b/"agent.py").write_bytes((b/"agent.py").read_bytes()+b"\n# candidate")
    pin=_reinventory(b)
    _archive(b,new)
    result=compare_archives(old,new,_pin(a),pin)
    assert result["archive_bytes_changed"] and result["requires_review_before_upgrade"]
    assert result["generated_file_changes"]["changed"][0]["path"]=="agent.py"
    assert result["both_archive_inventories_closed"] and not result["archives_extracted"]
    p=subprocess.run([sys.executable,"-S",str(a/"verify_release.py"),"--archive",str(old),
        "--manifest-sha256",_pin(a),"--compare-archive",str(new),"--compare-manifest-sha256",pin],
        capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)==result


@pytest.mark.parametrize("extra",["../outside","/absolute","C:/bad","bad\\path","AGENT.PY",
    "CON.txt","bad.","agent.py/child","untracked.py","folder/"])
def test_archive_refuses_unsafe_or_untracked_members(tmp_path,extra):
    from shared.process_agent_release import verify_archive
    root=tmp_path/"generated";_extract(root)
    path=_archive(root,tmp_path/"bad.zip",[(extra,b"x")])
    with pytest.raises(ReleaseIntegrityError):verify_archive(path,_pin(root))


def test_archive_refuses_duplicate_symlink_missing_and_corrupt_members(tmp_path):
    from shared.process_agent_release import verify_archive
    from zipfile import ZipInfo
    import stat
    root=tmp_path/"generated";_extract(root);pin=_pin(root)
    with pytest.warns(UserWarning,match="Duplicate name"):
        duplicate=_archive(root,tmp_path/"duplicate.zip",[("agent.py",b"duplicate")])
    link=ZipInfo("agent.py");link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
    symlink=_archive(root,tmp_path/"symlink.zip",[(link,b"/private")],omit=("agent.py",))
    missing=_archive(root,tmp_path/"missing.zip",omit=("agent.py",))
    corrupt=_archive(root,tmp_path/"corrupt.zip",[("agent.py",b"changed")],omit=("agent.py",))
    for p in (duplicate,symlink,missing,corrupt):
        with pytest.raises(ReleaseIntegrityError):verify_archive(p,pin)
    broken=tmp_path/"broken.zip";broken.write_bytes(b"not a zip")
    with pytest.raises(ReleaseIntegrityError):verify_archive(broken,pin)


def test_archive_bounds_before_payload_and_no_extraction(tmp_path,monkeypatch):
    from shared import process_agent_release as release
    root=tmp_path/"generated";_extract(root);pin=_pin(root)
    path=_archive(root,tmp_path/"valid.zip")
    monkeypatch.setattr(ZipFile,"extract",lambda *a,**k:pytest.fail("unexpected extraction"))
    monkeypatch.setattr(ZipFile,"extractall",lambda *a,**k:pytest.fail("unexpected extraction"))
    assert release.verify_archive(path,pin)["archive_inventory_closed"]
    monkeypatch.setattr(release,"MAX_TOTAL_EXPANDED_BYTES",1)
    with pytest.raises(ReleaseIntegrityError,match="total expanded"):
        release.verify_archive(path,pin)
    monkeypatch.setattr(release,"MAX_ARCHIVE_BYTES",1)
    with pytest.raises(ReleaseIntegrityError,match="archive byte"):
        release.verify_archive(path,pin)
