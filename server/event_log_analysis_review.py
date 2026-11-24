"""Review the case population of a previously displayed local analysis group."""
from __future__ import annotations

from contextlib import nullcontext
import re
from typing import Any

from . import event_log_activity_mappings as mappings
from . import event_log_hidden_activities as hidden
from . import event_log_quality as quality
from . import event_log_query as analysis
from . import event_log_workspace as workspace
from . import process_discovery_provenance as provenance

DIGEST = re.compile(r"[0-9a-f]{64}")


def _case_review_digest(conn, log_id: str, spec: dict, source_digest: str) -> str:
    # The raw source digest excludes local workspace settings. Pin the
    # applied mapping, hidden label list and tag membership as well.
    mappings._ensure(conn)
    hidden._ensure(conn)
    mapping = conn.execute(
        "SELECT definition FROM activity_mappings WHERE log_id = ? AND applied", [log_id]
    ).fetchone()
    hidden_labels = conn.execute(
        "SELECT activity FROM hidden_activities WHERE log_id = ? ORDER BY activity", [log_id]
    ).fetchall()
    tags = quality._set_digest(conn, """
        SELECT sha256(to_json(struct_pack(id := case_id, label := tag))) AS h
        FROM case_tags WHERE log_id = ?
    """, [log_id])
    return provenance.digest({
        "format": "openworkgraph.case-analysis-review.v1", "log_id": log_id, "spec": spec,
        "source": source_digest, "mapping": mapping[0] if mapping else None,
        "hidden": hidden_labels, "tag_membership": tags,
    })


def _bound(log_id: str, spec: Any, page: tuple[str, int] | None = None,
           expected: str | None = None, *, control=None) -> dict[str, Any]:
    parsed = analysis.parse(spec)
    normalized = parsed.model_dump(mode="json")
    if expected is not None and parsed.level != "cases":
        raise workspace.EventLogWorkspaceError("Review cases is available for case-level analyses.")
    # The same reentrant lock covers fingerprint and actual analysis. The job
    # deadline includes waiting for this lock and the digest computation itself.
    with control.workspace_lock(workspace._LOCK) if control is not None else workspace._LOCK:
        conn = workspace._connect()
        try:
            with control.connection(conn) if control is not None else nullcontext(conn) as reader:
                workspace._check_log(reader, log_id)
                if provenance.source_rows(reader, log_id) > provenance.MAX_SOURCE_ROWS:
                    raise workspace.EventLogWorkspaceError(
                        "Source exceeds complete fingerprint budget; source-bound export unavailable")
                source_digest = provenance.source_digest(reader, log_id)
                # Events can also be restricted by a case cohort. The raw
                # source fingerprint alone does not include the local tag,
                # mapping or visibility state that shapes these results.
                view_digest = _case_review_digest(reader, log_id, normalized, source_digest)
                case_digest = view_digest if parsed.level == "cases" else None
        finally:
            conn.close()
        if expected is not None and expected != case_digest:
            raise workspace.EventLogWorkspaceError(
                "The reviewed source or case view changed. Run the analysis again before opening its cases.")
        options = {"control": control} if control is not None else {}
        if page is not None:
            options["group_page"] = page
        result = analysis.run(log_id, normalized, **options)
    result["source_content_sha256"] = source_digest
    result["analysis_view_sha256"] = view_digest
    result["analysis_view_fingerprint_basis"] = "raw_source_normalized_spec_applied_mapping_hidden_activities_case_tags"
    if case_digest is not None:
        result["case_review_sha256"] = case_digest
        if page is None:
            dimensions = len(parsed.dimensions)
            result["case_review_groups"] = [
                analysis.case_group_digest(row[:dimensions]) for row in result["rows"]
            ]
    return result


def source_bound(log_id: str, spec: Any, *, control=None) -> dict[str, Any]:
    return _bound(log_id, spec, control=control)


def group_cases(log_id: str, body: Any) -> dict[str, Any]:
    keys = {"spec", "group_sha256", "offset", "expected_case_review_sha256"}
    if not isinstance(body, dict) or set(body) != keys:
        raise workspace.EventLogWorkspaceError("Provide the reviewed analysis, group digest, offset and case review digest.")
    digest, group, offset = body["expected_case_review_sha256"], body["group_sha256"], body["offset"]
    if (not isinstance(digest, str) or not DIGEST.fullmatch(digest)
            or not isinstance(group, str) or not DIGEST.fullmatch(group)
            or type(offset) is not int or not 0 <= offset <= provenance.MAX_SOURCE_ROWS):
        raise workspace.EventLogWorkspaceError("Invalid case review digest, group digest or offset.")
    return _bound(log_id, body["spec"], (group, offset), digest)
