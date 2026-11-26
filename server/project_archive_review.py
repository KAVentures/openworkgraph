"""Read-only reconciliation of original tracked agent project ZIP downloads.

The outer archive digest must match a retained project record before any ZIP
member is interpreted. Never extract or execute a supplied archive.
"""
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
import stat
from zipfile import BadZipFile, ZipFile

from shared import process_agent_release as release
from . import automation_pipeline as pipeline
from . import opportunity_projects as projects

MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
LINEAGE = "opportunity.lineage.json"


def _retained(conn, idea_id, digest):
    rows = conn.execute(
        "SELECT project_id,idea_revision,created_at,process_reference,spec_sha256,"
        "archive_sha256,base_archive_sha256 FROM idea_projects "
        "WHERE idea_id=? AND archive_sha256=?", [idea_id, digest]).fetchall()
    if len(rows) != 1:
        raise pipeline.PipelineError("archive does not match one retained project export for this idea")
    return rows[0]


def _inspect(content, row, idea_id):
    project_id, revision, _, reference_json, spec_hash, _, base_hash = row
    reference = json.loads(reference_json)
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if not 2 <= len(entries) <= release.MAX_FILES + 2:
                raise ValueError("archive member limit")
            names = [entry.filename for entry in entries]
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError("duplicate archive member")
            if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("expanded archive limit")
            files = {}
            for entry in entries:
                name = entry.filename
                if (name not in {release.MANIFEST_NAME, LINEAGE} and not release._safe_name(name)
                        or entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16)
                        or entry.flag_bits & 1 or entry.file_size > release.MAX_FILE_BYTES):
                    raise ValueError("unsupported archive member")
                with archive.open(entry) as source:
                    raw = source.read(release.MAX_FILE_BYTES + 1)
                if len(raw) != entry.file_size:
                    raise ValueError("archive member size mismatch")
                files[name] = raw.decode("utf-8")
        lineage = json.loads(files.pop(LINEAGE), object_pairs_hook=release._unique)
        expected = {
            "format": "openworkgraph.opportunity-project-link.v1",
            "idea_id": idea_id, "idea_revision": revision,
            "process_reference": reference, "spec_sha256": spec_hash,
            "base_archive_sha256": base_hash, "execution_authorized": False,
            "business_outcomes_verified": False, "independent_design_review_verified": False,
        }
        if project_id != "project_" + projects._digest(expected) or lineage != dict(expected, project_id=project_id):
            raise ValueError("lineage mismatch")
        spec = json.loads(files["workflow.spec.json"], object_pairs_hook=release._unique)
        if (not isinstance(spec, dict)
                or spec.get("format") != "openworkgraph.agent-runtime-project.v1"
                or spec.get("execution_authorization") is not False
                or spec.get("source_records_included") is not False
                or {k: spec.get(k) for k in reference} != reference
                or projects._digest(spec) != spec_hash):
            raise ValueError("spec mismatch")
        manifest = files.pop(release.MANIFEST_NAME, None)
        if manifest is None:
            # Historical exports predate the generated file inventory. The
            # outer recorded digest still pins every original archive byte.
            status, file_count, manifest_hash = "legacy_archive_without_file_manifest", None, None
        else:
            # Recreate the canonical manifest from every member's exact UTF-8
            # bytes. Unlike verify_directory, this checks the complete inventory
            # including unexpected files, without filesystem extraction.
            canonical = release.create_manifest(files, spec)
            if json.loads(manifest, object_pairs_hook=release._unique) != json.loads(canonical):
                raise ValueError("file inventory mismatch")
            status, file_count = "complete_generated_file_inventory_matches", len(files)
            manifest_hash = sha256(manifest.encode("utf-8")).hexdigest()
    except (BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError, KeyError, TypeError, RecursionError):
        raise pipeline.PipelineError("retained archive structure or generated file inventory does not match") from None
    return status, file_count, manifest_hash


def review_archive(idea_id: str, content: bytes):
    if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_ARCHIVE_BYTES:
        raise pipeline.PipelineError("select the original project ZIP of at most 16 MiB")
    digest = sha256(content).hexdigest()
    with pipeline._LOCK, closing(pipeline._connect()) as conn:
        pipeline._get(conn, idea_id)
        row = _retained(conn, idea_id, digest)
    status, count, manifest_hash = _inspect(content, row, idea_id)
    with pipeline._LOCK, closing(pipeline._connect()) as conn:
        idea = pipeline._public(pipeline._get(conn, idea_id))
        if _retained(conn, idea_id, digest) != row:
            raise pipeline.PipelineError("retained project changed during archive review")
    process = pipeline._process_status(json.loads(row[3]))
    return {
        "format": "openworkgraph.tracked-project-archive-review.v1",
        "project_id": row[0], "archive_sha256": digest, "exported_at": row[2],
        "spec_sha256": row[4], "idea_revision_at_export": row[1],
        "current_idea_revision": idea["revision"],
        "idea_revision_matches_export": idea["revision"] == row[1],
        "process_reference_status": process["status"],
        "exact_retained_archive_bytes_match": True,
        "generated_file_inventory_status": status,
        "verified_generated_file_count": count,
        "release_manifest_sha256": manifest_hash,
        "archive_extracted": False, "archive_persisted": False,
        "generated_code_executed": False, "independent_signature_verified": False,
        "deployment_verified": False, "business_outcomes_verified": False,
        "execution_authorized": False,
    }
