"""Local digest/selection references from supplied logs to reviewed knowledge.

Snapshots retain no graph or case copies. A selected reference to an edited
graph is not independent proof of derivation, business rules or execution.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from shared.process_model import ProcessModel
from . import event_log_workspace as workspace, event_log_quality as quality

PREFIX = 'urn:openworkgraph:local-log-discovery:'
MAX_SNAPSHOTS = 100
MAX_SOURCE_ROWS = 30_000_000
MAX_CHECKED_LOGS = 3
MAX_PROJECTION_ROWS = 30_000_000
MAX_CHECKED_PROJECTIONS = 6
MAX_INDEXED_CASES = 1_000
MAX_INDEXED_EVENTS = 20_000
_REF = re.compile(re.escape(PREFIX) + r'[0-9a-f]{64}$')


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def model_digest(model):
    # Generated unsaved drafts deliberately have an empty business goal. Do not
    # make them saveable or supply a business rule merely to calculate a hash.
    return digest(model.model_dump(mode='json') if isinstance(model, ProcessModel) else model)


def source_digest(conn, log_id):
    # JSON structures escape separators and include object types/attributes,
    # which can affect case notions and filters even when events are unchanged.
    queries = (
        'SELECT sha256(to_json(struct_pack(id:=event_id,activity:=activity,at:=ts,resource:=resource,attributes:=attributes))) AS h FROM events WHERE log_id=?',
        'SELECT sha256(to_json(struct_pack(id:=object_id,type:=object_type,attributes:=attributes))) AS h FROM objects WHERE log_id=?',
        'SELECT sha256(to_json(struct_pack(event:=event_id,object:=object_id,qualifier:=qualifier))) AS h FROM event_objects WHERE log_id=?',
    )
    return digest({'algorithm': 'openworkgraph.discovery-source.v1',
                   'tables': [quality._set_digest(conn, query, [log_id]) for query in queries]})


def source_rows(conn, log_id):
    return sum(conn.execute(f'SELECT count(*) FROM {table} WHERE log_id=?', [log_id]).fetchone()[0]
               for table in ('events', 'objects', 'event_objects'))


def projected_input(conn):
    """Pin the actual selected case-event projection after all view transforms.

    Includes case membership, event identity, effective activity, timestamp,
    resource and attributes. No row contents are persisted in the manifest.
    """
    events, cases = conn.execute("SELECT count(*), count(DISTINCT case_id) FROM ce").fetchone()
    if events > MAX_PROJECTION_ROWS:
        raise workspace.EventLogWorkspaceError(
            f"effective discovery input exceeds the {MAX_PROJECTION_ROWS:,}-row fingerprint budget")
    rows_digest = quality._set_digest(conn, """
        SELECT sha256(to_json(struct_pack(case_key:=case_id, event_key:=event_id,
            activity:=activity, at:=ts, resource:=resource, attributes:=attributes))) AS h FROM ce
    """, [])
    return {"format": "openworkgraph.discovery-projection.v1",
            "sha256": digest({"format": "openworkgraph.discovery-projection.v1",
                              "rows": rows_digest, "events": events, "cases": cases}),
            "events": events, "cases": cases}


def case_digest_index(conn, log_id, effective):
    """Complete bounded case fingerprints, with no raw case keys retained.

    The log-scoped hash is an opaque local lookup key, not anonymization.
    The current index carries live case IDs only inside the local request.
    Large selections get no partial index.
    """
    if effective["cases"] > MAX_INDEXED_CASES or effective["events"] > MAX_INDEXED_EVENTS:
        return None
    rows = conn.execute("""
        SELECT case_id, sha256(string_agg(h, '' ORDER BY h)) FROM (
          SELECT case_id, sha256(to_json(struct_pack(event_key:=event_id, activity:=activity,
                    at:=ts, resource:=resource, attributes:=attributes))) AS h FROM ce
        ) GROUP BY case_id ORDER BY case_id
    """).fetchall()
    result = {}
    for case_id, sha256 in rows:
        key = digest({"format": "openworkgraph.discovery-case-key.v1", "log_id": log_id, "case_id": case_id})
        if key in result:
            raise workspace.EventLogWorkspaceError("Ambiguous discovery case identity")
        result[key] = (case_id, sha256)
    if len(result) != effective["cases"]:
        raise workspace.EventLogWorkspaceError("Discovery case index does not match the effective input")
    return result


def compare_case_index(saved, current_index):
    """Summaries and current per-case status; removed raw keys cannot be reconstructed."""
    original = saved.get("case_digest_index")
    if original is None:
        return {"status": "not_recorded"}, {}
    if current_index is None:
        return {"status": "current_work_budget_exceeded"}, {}
    original_count = saved.get("effective_input", {}).get("cases")
    if (not isinstance(original, dict) or len(original) != original_count
            or len(original) > MAX_INDEXED_CASES
            or any(not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key)
                   or not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                   for key, value in original.items())):
        return {"status": "unavailable"}, {}
    by_case = {}
    counts = {"added": 0, "modified": 0, "unchanged": 0}
    for key, (case_id, sha256) in current_index.items():
        status = "added" if key not in original else "unchanged" if original[key] == sha256 else "modified"
        by_case[case_id] = status
        counts[status] += 1
    return {"status": "complete", **counts, "removed": len(set(original) - set(current_index)),
            "original_cases": len(original), "current_cases": len(current_index),
            "removed_case_ids_retained": False}, by_case


def materialize_selection(conn, log_id, selection):
    from . import event_log_cohorts as cohorts
    from .event_log_hidden_activities import apply_to_case_events as hide
    from .event_log_activity_mappings import apply_to_case_events as map_activities

    notion = selection["case_notion"]
    if workspace._case_type(conn, log_id, notion) != notion:
        raise workspace.EventLogWorkspaceError("Original case notion is unavailable")
    conn.execute(f"CREATE OR REPLACE TEMP TABLE ce AS {workspace._CASE_EVENTS}", [log_id, notion])
    hide(conn, log_id)
    map_activities(conn, log_id)
    cohorts.restrict(conn, cohorts.parse_cohort(selection["cohort"]), log_id)


def retain(conn, log_id, notion, result, expected_model_sha256, expected_projection_sha256=None):
    if result['process_model_sha256'] != expected_model_sha256:
        raise workspace.EventLogWorkspaceError('discovery changed; discover again before recording its source snapshot')
    rows = source_rows(conn, log_id)
    if rows > MAX_SOURCE_ROWS:
        raise workspace.EventLogWorkspaceError(f'source exceeds the {MAX_SOURCE_ROWS:,}-row fingerprint budget; no partial snapshot recorded')
    projection = result.get('effective_input') or projected_input(conn)
    if expected_projection_sha256 is not None and projection['sha256'] != expected_projection_sha256:
        raise workspace.EventLogWorkspaceError('effective discovery input changed; discover again before recording its source snapshot')
    selection = {'case_notion': notion, 'cohort': result['cohort'], 'settings': result['settings']}
    payload = {'format': 'openworkgraph.log-discovery-snapshot.v1', 'log_id': log_id,
               'source_content_sha256': source_digest(conn, log_id), 'selection': selection,
               'selection_sha256': digest(selection), 'discovered_model_sha256': result['process_model_sha256'],
               'quality': result['quality'], 'discovery_method': result['discovery_method'], 'source_rows': rows,
               'effective_input': projection}
    indexed = case_digest_index(conn, log_id, projection)
    if indexed is not None:
        payload["case_digest_index"] = {key: value[1] for key, value in indexed.items()}

    # Preserve the original task-mining snapshot identity, where present.
    # This pins the connection to recorded execution evidence without
    # copying source cases or permitting a new history read.
    observed = workspace._check_log(conn, log_id).get("observed_execution_snapshot")
    if observed and observed.get("manifest_available") is not True:
        raise workspace.EventLogWorkspaceError("Original observed execution snapshot manifest is unavailable")
    if observed:
        payload["observed_execution_origin"] = {
            "snapshot_sha256": observed["snapshot_sha256"],
            "original_case_count": observed["original_case_count"],
            "original_event_count": observed["original_event_count"],
            "business_case_identity_verified": False,
            "capture_complete": False,
        }
    reference = PREFIX + digest(payload)
    old = conn.execute('SELECT created_at FROM discovery_snapshots WHERE reference=?', [reference]).fetchone()
    if old:
        at = old[0]
    else:
        if conn.execute('SELECT count(*) FROM discovery_snapshots WHERE log_id=?', [log_id]).fetchone()[0] >= MAX_SNAPSHOTS:
            raise workspace.EventLogWorkspaceError(f'at most {MAX_SNAPSHOTS} discovery snapshots per log')
        at = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute('INSERT INTO discovery_snapshots VALUES (?,?,?,?)', [log_id, reference, at, json.dumps(payload)])
    return {'reference': reference, 'recorded_at': at.isoformat() + 'Z', 'max_snapshots': MAX_SNAPSHOTS,
            'source_content_sha256': payload['source_content_sha256'], 'selection_sha256': payload['selection_sha256'], 'effective_input': projection,
            'graph_and_case_copies_retained': False, 'execution_authorized': False}


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_model_sha256: str = Field(pattern=r'^[0-9a-f]{64}
    case_type: str | None = Field(default=None, max_length=200)
    noise: float = Field(default=0.2, ge=0, lt=1, strict=True)
    activities: float = Field(default=1.0, gt=0, le=1, strict=True)
    cohort: dict = Field(default_factory=dict)


def record_draft(log_id, payload):
    from . import event_log_model_discovery as discovery
    try:
        request = DraftRequest.model_validate(payload)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid discovery snapshot request') from None
    return discovery.discover(log_id, request.case_type, noise=request.noise, activities=request.activities,
                              cohort=request.cohort, _record_snapshot=True,
                              _expected_model_sha256=request.expected_model_sha256,
                              _expected_projection_sha256=request.expected_projection_sha256)


class SelectionInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str = Field(pattern=r"^urn:openworkgraph:local-log-discovery:[a-f0-9]{64}$")
    expected_process_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_projection_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    after: str | None = Field(default=None, max_length=512)
    case_id: str | None = Field(default=None, min_length=1, max_length=512)
    offset: int = Field(default=0, ge=0, le=MAX_PROJECTION_ROWS)
    limit: int = Field(default=30, ge=1, le=100)
    case_status: str = Field(default='all', pattern=r'^(all|added|modified|unchanged)


def inspect_selection(record, payload):
    """Reopen precisely the source selection bound to a saved process revision.

    Pages are pinned to a fresh effective-input digest. Historical row copies
    were never retained, so changed projections are explicitly current data.
    Only local authenticated dashboard callers may inspect case identities.
    """
    try:
        request = SelectionInspection.model_validate(payload)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("invalid exact source inspection request") from None
    if (record.get("content_sha256") != request.expected_process_sha256
            or request.reference not in record.get("evidence_refs", [])):
        raise workspace.EventLogWorkspaceError("Source reference does not belong to the exact saved process revision")
    if (request.case_id is not None and (request.after is not None or request.case_status != "all")
            or request.case_id is None and request.offset != 0):
        raise workspace.EventLogWorkspaceError("Choose either a case page or an event page")
    if (request.after is not None or request.case_id is not None) and not request.expected_projection_sha256:
        raise workspace.EventLogWorkspaceError("Open the original selection before paging through it")
    if not workspace.database_path().exists():
        raise workspace.EventLogWorkspaceError("Original discovery source is unavailable")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            row = conn.execute(
                "SELECT log_id, payload FROM discovery_snapshots WHERE reference = ?",
                [request.reference]).fetchone()
            if row is None:
                raise workspace.EventLogWorkspaceError("Original discovery source is unavailable")
            log_id, raw = row
            try:
                saved = json.loads(raw)
                if (saved["log_id"] != log_id or PREFIX + digest(saved) != request.reference
                        or digest(saved["selection"]) != saved["selection_sha256"]):
                    raise ValueError
            except (KeyError, TypeError, ValueError, RecursionError):
                raise workspace.EventLogWorkspaceError("Discovery source metadata does not match its reference") from None
            info = workspace._check_log(conn, log_id)
            if source_rows(conn, log_id) > MAX_SOURCE_ROWS:
                raise workspace.EventLogWorkspaceError("Original source exceeds the inspection work budget")
            materialize_selection(conn, log_id, saved["selection"])
            current = projected_input(conn)
            if request.expected_projection_sha256 and current["sha256"] != request.expected_projection_sha256:
                raise workspace.EventLogWorkspaceError("Effective discovery input changed since inspection; reopen the original selection")
            original = saved.get("effective_input")
            current_index = case_digest_index(conn, log_id, current) if "case_digest_index" in saved else None
            case_drift, statuses = compare_case_index(saved, current_index)
            if request.case_status != "all" and case_drift["status"] != "complete":
                raise workspace.EventLogWorkspaceError("Complete case comparison is unavailable for this selection")
            result = {
                "format": "openworkgraph.discovery-selection-inspection.v1",
                "reference": request.reference,
                "workflow_id": record["workflow_id"], "revision": record["revision"],
                "process_sha256": record["content_sha256"],
                "log_id": log_id, "selection": saved["selection"],
                "selection_sha256": saved["selection_sha256"],
                "original_effective_input": original,
                "current_effective_input": current,
                "effective_input_status": "not_recorded" if original is None else
                    "matches" if original == current else "changed",
                "historical_rows_retained": False,
                "raw_source_fingerprint_rechecked": False,
                "case_drift": case_drift,
                "observed_execution_snapshot": info.get("observed_execution_snapshot"),
                "business_case_identity_verified": False,
                "business_outcomes_verified": False,
                "execution_authorized": False,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            if request.case_id is None:
                # Keyset pagination across exact currently selected complete cases.
                clauses, params = [], []
                if request.after is not None:
                    clauses.append("case_id > ?")
                    params.append(request.after)
                if request.case_status != "all":
                    matching_keys = [key for key, status in statuses.items() if status == request.case_status]
                    if matching_keys:
                        clauses.append("list_contains(?, case_id)")
                        params.append(matching_keys)
                    else:
                        clauses.append("FALSE")
                where = "WHERE " + " AND ".join(clauses) if clauses else ""
                rows = conn.execute(f"""
                    SELECT case_id, count(*), epoch(max(ts)-min(ts)),
                           count(*) - count(DISTINCT ts)
                    FROM ce {where}
                    GROUP BY case_id ORDER BY case_id LIMIT ?
                """, [*params, request.limit + 1]).fetchall()
                result.update(mode="cases", case_status=request.case_status,
                              matching_cases=(current["cases"] if request.case_status == "all" else case_drift[request.case_status]),
                              cases=[{"case_id": key, "events": n, "elapsed_seconds": elapsed,
                                      "timestamp_ties": ties, "comparison_status": statuses.get(key, "unknown")}
                                     for key, n, elapsed, ties in rows[:request.limit]],
                              next_after=rows[request.limit - 1][0] if len(rows) > request.limit else None)
            else:
                total = conn.execute("SELECT count(*) FROM ce WHERE case_id=?", [request.case_id]).fetchone()[0]
                if not total:
                    raise workspace.EventLogWorkspaceError("Case is not in the exact discovery selection")
                if request.offset >= total:
                    raise workspace.EventLogWorkspaceError("Selected event page is unavailable")
                rows = conn.execute("""
                    SELECT event_id, activity, ts FROM ce WHERE case_id=?
                    ORDER BY ts, event_id LIMIT ? OFFSET ?
                """, [request.case_id, request.limit, request.offset]).fetchall()
                result.update(mode="events", case_id=request.case_id, total_events=total,
                              comparison_status=statuses.get(request.case_id, "unknown"),
                              offset=request.offset,
                              events=[{"event_id": key, "activity": activity,
                                       "timestamp": stamp.isoformat() + "Z"}
                                      for key, activity, stamp in rows],
                              next_offset=request.offset + len(rows) if request.offset + len(rows) < total else None)
            return result
        finally:
            conn.close()


def origins(record, *, include_selection=False):
    refs = list(dict.fromkeys(r for r in record.get('evidence_refs', []) if r.startswith(PREFIX)))
    if not refs:
        return []
    results = []
    try:
        if not workspace.database_path().exists():
            return [{'reference': ref, 'status': 'unavailable'} for ref in refs]
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                current, checked_times, checked_rows = {}, {}, 0
                projections, projection_budget = {}, 0
                for ref in refs:
                    row = conn.execute('SELECT log_id,created_at,payload FROM discovery_snapshots WHERE reference=?',
                                       [ref]).fetchone() if _REF.fullmatch(ref) else None
                    if row is None:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    log, at, raw = row
                    saved = json.loads(raw)
                    if saved.get('log_id') != log or PREFIX + digest(saved) != ref:
                        results.append({'reference': ref, 'status': 'reference_mismatch'}); continue
                    try:
                        log_info = workspace._check_log(conn, log)
                    except workspace.EventLogWorkspaceError:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    if log not in current:
                        rows = source_rows(conn, log) if len(current) < MAX_CHECKED_LOGS else MAX_SOURCE_ROWS + 1
                        if checked_rows + rows <= MAX_SOURCE_ROWS:
                            current[log] = source_digest(conn, log)
                            checked_times[log] = datetime.now(timezone.utc).isoformat()
                            checked_rows += rows
                        else:
                            current[log] = None
                    result = {key: saved[key] for key in ('log_id', 'source_content_sha256', 'selection_sha256',
                                                         'discovered_model_sha256', 'quality', 'discovery_method')}
                    result.update(reference=ref, recorded_at=at.isoformat() + 'Z',
                                  source_fingerprint_checked_at=checked_times.get(log),
                                  status=('not_checked_work_budget' if current[log] is None else
                                          'current' if current[log] == saved['source_content_sha256'] else 'source_advanced'),
                                  reviewed_graph_matches_discovery=model_digest(record['process_model']) == saved['discovered_model_sha256'],
                                  association_basis='user_selected_snapshot_reference', derivation_independently_verified=False,
                                  business_rules_verified=False, execution_authorized=False)
                    pinned_input = saved.get("effective_input")
                    if pinned_input:
                        result["effective_input"] = pinned_input
                        key = (log, saved["selection_sha256"])
                        if key not in projections:
                            # Bound total work by all underlying source rows,
                            # not just the number of rows that survive the filter.
                            try:
                                work = source_rows(conn, log)
                                if (current[log] is None or len(projections) >= MAX_CHECKED_PROJECTIONS
                                        or projection_budget + work > MAX_PROJECTION_ROWS):
                                    projections[key] = ("not_checked_work_budget", None)
                                else:
                                    projection_budget += work
                                    materialize_selection(conn, log, saved["selection"])
                                    projections[key] = ("checked", projected_input(conn))
                            except workspace.EventLogWorkspaceError:
                                projections[key] = ("unavailable", None)
                        checked, effective = projections[key]
                        result["effective_input_status"] = (
                            checked if effective is None else
                            "matches" if effective == pinned_input else "changed")
                        if effective is not None:
                            result["current_effective_input"] = effective
                        if result["status"] == "current" and result["effective_input_status"] != "matches":
                            result["status"] = ("projection_changed" if checked == "checked" else
                                                "projection_not_checked")
                    else:
                        result["effective_input_status"] = "not_recorded"
                    if "observed_execution_origin" in saved:
                        pinned = saved["observed_execution_origin"]
                        retained = log_info.get("observed_execution_snapshot")
                        matching = bool(retained and retained.get("manifest_available") is True and all(
                            retained.get(key) == pinned.get(key)
                            for key in ("snapshot_sha256", "original_case_count", "original_event_count",
                                        "business_case_identity_verified", "capture_complete")
                        ))
                        result["observed_execution_origin"] = {
                            **pinned,
                            "retained_manifest_status": "matches" if matching else "unavailable_or_changed",
                            "original_history_rechecked": False,
                            "business_outcomes_verified": False,
                        }
                    if include_selection:
                        result['selection'] = saved['selection']
                    results.append(result)
            finally:
                conn.close()
        return results
    except Exception:
        return [{'reference': ref, 'status': 'check_failed'} for ref in refs]
)
    expected_projection_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}
    case_type: str | None = Field(default=None, max_length=200)
    noise: float = Field(default=0.2, ge=0, lt=1, strict=True)
    activities: float = Field(default=1.0, gt=0, le=1, strict=True)
    cohort: dict = Field(default_factory=dict)


def record_draft(log_id, payload):
    from . import event_log_model_discovery as discovery
    try:
        request = DraftRequest.model_validate(payload)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid discovery snapshot request') from None
    return discovery.discover(log_id, request.case_type, noise=request.noise, activities=request.activities,
                              cohort=request.cohort, _record_snapshot=True,
                              _expected_model_sha256=request.expected_model_sha256)


def origins(record, *, include_selection=False):
    refs = list(dict.fromkeys(r for r in record.get('evidence_refs', []) if r.startswith(PREFIX)))
    if not refs:
        return []
    results = []
    try:
        if not workspace.database_path().exists():
            return [{'reference': ref, 'status': 'unavailable'} for ref in refs]
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                current, checked_times, checked_rows = {}, {}, 0
                for ref in refs:
                    row = conn.execute('SELECT log_id,created_at,payload FROM discovery_snapshots WHERE reference=?',
                                       [ref]).fetchone() if _REF.fullmatch(ref) else None
                    if row is None:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    log, at, raw = row
                    saved = json.loads(raw)
                    if saved.get('log_id') != log or PREFIX + digest(saved) != ref:
                        results.append({'reference': ref, 'status': 'reference_mismatch'}); continue
                    try:
                        log_info = workspace._check_log(conn, log)
                    except workspace.EventLogWorkspaceError:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    if log not in current:
                        rows = source_rows(conn, log) if len(current) < MAX_CHECKED_LOGS else MAX_SOURCE_ROWS + 1
                        if checked_rows + rows <= MAX_SOURCE_ROWS:
                            current[log] = source_digest(conn, log)
                            checked_times[log] = datetime.now(timezone.utc).isoformat()
                            checked_rows += rows
                        else:
                            current[log] = None
                    result = {key: saved[key] for key in ('log_id', 'source_content_sha256', 'selection_sha256',
                                                         'discovered_model_sha256', 'quality', 'discovery_method')}
                    result.update(reference=ref, recorded_at=at.isoformat() + 'Z',
                                  source_fingerprint_checked_at=checked_times.get(log),
                                  status=('not_checked_work_budget' if current[log] is None else
                                          'current' if current[log] == saved['source_content_sha256'] else 'source_advanced'),
                                  reviewed_graph_matches_discovery=model_digest(record['process_model']) == saved['discovered_model_sha256'],
                                  association_basis='user_selected_snapshot_reference', derivation_independently_verified=False,
                                  business_rules_verified=False, execution_authorized=False)
                    if "observed_execution_origin" in saved:
                        pinned = saved["observed_execution_origin"]
                        retained = log_info.get("observed_execution_snapshot")
                        matching = bool(retained and retained.get("manifest_available") is True and all(
                            retained.get(key) == pinned.get(key)
                            for key in ("snapshot_sha256", "original_case_count", "original_event_count",
                                        "business_case_identity_verified", "capture_complete")
                        ))
                        result["observed_execution_origin"] = {
                            **pinned,
                            "retained_manifest_status": "matches" if matching else "unavailable_or_changed",
                            "original_history_rechecked": False,
                            "business_outcomes_verified": False,
                        }
                    if include_selection:
                        result['selection'] = saved['selection']
                    results.append(result)
            finally:
                conn.close()
        return results
    except Exception:
        return [{'reference': ref, 'status': 'check_failed'} for ref in refs]
)
    case_type: str | None = Field(default=None, max_length=200)
    noise: float = Field(default=0.2, ge=0, lt=1, strict=True)
    activities: float = Field(default=1.0, gt=0, le=1, strict=True)
    cohort: dict = Field(default_factory=dict)


def record_draft(log_id, payload):
    from . import event_log_model_discovery as discovery
    try:
        request = DraftRequest.model_validate(payload)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid discovery snapshot request') from None
    return discovery.discover(log_id, request.case_type, noise=request.noise, activities=request.activities,
                              cohort=request.cohort, _record_snapshot=True,
                              _expected_model_sha256=request.expected_model_sha256)


def origins(record, *, include_selection=False):
    refs = list(dict.fromkeys(r for r in record.get('evidence_refs', []) if r.startswith(PREFIX)))
    if not refs:
        return []
    results = []
    try:
        if not workspace.database_path().exists():
            return [{'reference': ref, 'status': 'unavailable'} for ref in refs]
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                current, checked_times, checked_rows = {}, {}, 0
                for ref in refs:
                    row = conn.execute('SELECT log_id,created_at,payload FROM discovery_snapshots WHERE reference=?',
                                       [ref]).fetchone() if _REF.fullmatch(ref) else None
                    if row is None:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    log, at, raw = row
                    saved = json.loads(raw)
                    if saved.get('log_id') != log or PREFIX + digest(saved) != ref:
                        results.append({'reference': ref, 'status': 'reference_mismatch'}); continue
                    try:
                        log_info = workspace._check_log(conn, log)
                    except workspace.EventLogWorkspaceError:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    if log not in current:
                        rows = source_rows(conn, log) if len(current) < MAX_CHECKED_LOGS else MAX_SOURCE_ROWS + 1
                        if checked_rows + rows <= MAX_SOURCE_ROWS:
                            current[log] = source_digest(conn, log)
                            checked_times[log] = datetime.now(timezone.utc).isoformat()
                            checked_rows += rows
                        else:
                            current[log] = None
                    result = {key: saved[key] for key in ('log_id', 'source_content_sha256', 'selection_sha256',
                                                         'discovered_model_sha256', 'quality', 'discovery_method')}
                    result.update(reference=ref, recorded_at=at.isoformat() + 'Z',
                                  source_fingerprint_checked_at=checked_times.get(log),
                                  status=('not_checked_work_budget' if current[log] is None else
                                          'current' if current[log] == saved['source_content_sha256'] else 'source_advanced'),
                                  reviewed_graph_matches_discovery=model_digest(record['process_model']) == saved['discovered_model_sha256'],
                                  association_basis='user_selected_snapshot_reference', derivation_independently_verified=False,
                                  business_rules_verified=False, execution_authorized=False)
                    if "observed_execution_origin" in saved:
                        pinned = saved["observed_execution_origin"]
                        retained = log_info.get("observed_execution_snapshot")
                        matching = bool(retained and retained.get("manifest_available") is True and all(
                            retained.get(key) == pinned.get(key)
                            for key in ("snapshot_sha256", "original_case_count", "original_event_count",
                                        "business_case_identity_verified", "capture_complete")
                        ))
                        result["observed_execution_origin"] = {
                            **pinned,
                            "retained_manifest_status": "matches" if matching else "unavailable_or_changed",
                            "original_history_rechecked": False,
                            "business_outcomes_verified": False,
                        }
                    if include_selection:
                        result['selection'] = saved['selection']
                    results.append(result)
            finally:
                conn.close()
        return results
    except Exception:
        return [{'reference': ref, 'status': 'check_failed'} for ref in refs]
)


def inspect_selection(record, payload):
    """Reopen precisely the source selection bound to a saved process revision.

    Pages are pinned to a fresh effective-input digest. Historical row copies
    were never retained, so changed projections are explicitly current data.
    Only local authenticated dashboard callers may inspect case identities.
    """
    try:
        request = SelectionInspection.model_validate(payload)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("invalid exact source inspection request") from None
    if (record.get("content_sha256") != request.expected_process_sha256
            or request.reference not in record.get("evidence_refs", [])):
        raise workspace.EventLogWorkspaceError("Source reference does not belong to the exact saved process revision")
    if (request.case_id is not None and request.after is not None
            or request.case_id is None and request.offset != 0):
        raise workspace.EventLogWorkspaceError("Choose either a case page or an event page")
    if (request.after is not None or request.case_id is not None) and not request.expected_projection_sha256:
        raise workspace.EventLogWorkspaceError("Open the original selection before paging through it")
    if not workspace.database_path().exists():
        raise workspace.EventLogWorkspaceError("Original discovery source is unavailable")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            row = conn.execute(
                "SELECT log_id, payload FROM discovery_snapshots WHERE reference = ?",
                [request.reference]).fetchone()
            if row is None:
                raise workspace.EventLogWorkspaceError("Original discovery source is unavailable")
            log_id, raw = row
            try:
                saved = json.loads(raw)
                if (saved["log_id"] != log_id or PREFIX + digest(saved) != request.reference
                        or digest(saved["selection"]) != saved["selection_sha256"]):
                    raise ValueError
            except (KeyError, TypeError, ValueError, RecursionError):
                raise workspace.EventLogWorkspaceError("Discovery source metadata does not match its reference") from None
            workspace._check_log(conn, log_id)
            if source_rows(conn, log_id) > MAX_SOURCE_ROWS:
                raise workspace.EventLogWorkspaceError("Original source exceeds the inspection work budget")
            materialize_selection(conn, log_id, saved["selection"])
            current = projected_input(conn)
            if request.expected_projection_sha256 and current["sha256"] != request.expected_projection_sha256:
                raise workspace.EventLogWorkspaceError("Effective discovery input changed since inspection; reopen the original selection")
            original = saved.get("effective_input")
            result = {
                "format": "openworkgraph.discovery-selection-inspection.v1",
                "reference": request.reference,
                "workflow_id": record["workflow_id"], "revision": record["revision"],
                "process_sha256": record["content_sha256"],
                "log_id": log_id, "selection": saved["selection"],
                "selection_sha256": saved["selection_sha256"],
                "original_effective_input": original,
                "current_effective_input": current,
                "effective_input_status": "not_recorded" if original is None else
                    "matches" if original == current else "changed",
                "historical_rows_retained": False,
                "raw_source_fingerprint_rechecked": False,
                "business_case_identity_verified": False,
                "business_outcomes_verified": False,
                "execution_authorized": False,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            if request.case_id is None:
                # Keyset pagination across exact currently selected complete cases.
                where = "WHERE case_id > ?" if request.after is not None else ""
                params = [request.after] if request.after is not None else []
                rows = conn.execute(f"""
                    SELECT case_id, count(*), epoch(max(ts)-min(ts)),
                           count(*) - count(DISTINCT ts)
                    FROM ce {where}
                    GROUP BY case_id ORDER BY case_id LIMIT ?
                """, [*params, request.limit + 1]).fetchall()
                result.update(mode="cases",
                              cases=[{"case_id": key, "events": n, "elapsed_seconds": elapsed,
                                      "timestamp_ties": ties}
                                     for key, n, elapsed, ties in rows[:request.limit]],
                              next_after=rows[request.limit - 1][0] if len(rows) > request.limit else None)
            else:
                total = conn.execute("SELECT count(*) FROM ce WHERE case_id=?", [request.case_id]).fetchone()[0]
                if not total:
                    raise workspace.EventLogWorkspaceError("Case is not in the exact discovery selection")
                rows = conn.execute("""
                    SELECT event_id, activity, ts FROM ce WHERE case_id=?
                    ORDER BY ts, event_id LIMIT ? OFFSET ?
                """, [request.case_id, request.limit, request.offset]).fetchall()
                result.update(mode="events", case_id=request.case_id, total_events=total,
                              offset=request.offset,
                              events=[{"event_id": key, "activity": activity,
                                       "timestamp": stamp.isoformat() + "Z"}
                                      for key, activity, stamp in rows],
                              next_offset=request.offset + len(rows) if request.offset + len(rows) < total else None)
            return result
        finally:
            conn.close()


def origins(record, *, include_selection=False):
    refs = list(dict.fromkeys(r for r in record.get('evidence_refs', []) if r.startswith(PREFIX)))
    if not refs:
        return []
    results = []
    try:
        if not workspace.database_path().exists():
            return [{'reference': ref, 'status': 'unavailable'} for ref in refs]
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                current, checked_times, checked_rows = {}, {}, 0
                projections, projection_budget = {}, 0
                for ref in refs:
                    row = conn.execute('SELECT log_id,created_at,payload FROM discovery_snapshots WHERE reference=?',
                                       [ref]).fetchone() if _REF.fullmatch(ref) else None
                    if row is None:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    log, at, raw = row
                    saved = json.loads(raw)
                    if saved.get('log_id') != log or PREFIX + digest(saved) != ref:
                        results.append({'reference': ref, 'status': 'reference_mismatch'}); continue
                    try:
                        log_info = workspace._check_log(conn, log)
                    except workspace.EventLogWorkspaceError:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    if log not in current:
                        rows = source_rows(conn, log) if len(current) < MAX_CHECKED_LOGS else MAX_SOURCE_ROWS + 1
                        if checked_rows + rows <= MAX_SOURCE_ROWS:
                            current[log] = source_digest(conn, log)
                            checked_times[log] = datetime.now(timezone.utc).isoformat()
                            checked_rows += rows
                        else:
                            current[log] = None
                    result = {key: saved[key] for key in ('log_id', 'source_content_sha256', 'selection_sha256',
                                                         'discovered_model_sha256', 'quality', 'discovery_method')}
                    result.update(reference=ref, recorded_at=at.isoformat() + 'Z',
                                  source_fingerprint_checked_at=checked_times.get(log),
                                  status=('not_checked_work_budget' if current[log] is None else
                                          'current' if current[log] == saved['source_content_sha256'] else 'source_advanced'),
                                  reviewed_graph_matches_discovery=model_digest(record['process_model']) == saved['discovered_model_sha256'],
                                  association_basis='user_selected_snapshot_reference', derivation_independently_verified=False,
                                  business_rules_verified=False, execution_authorized=False)
                    pinned_input = saved.get("effective_input")
                    if pinned_input:
                        result["effective_input"] = pinned_input
                        key = (log, saved["selection_sha256"])
                        if key not in projections:
                            # Bound total work by all underlying source rows,
                            # not just the number of rows that survive the filter.
                            try:
                                work = source_rows(conn, log)
                                if (len(projections) >= MAX_CHECKED_PROJECTIONS
                                        or projection_budget + work > MAX_PROJECTION_ROWS):
                                    projections[key] = ("not_checked_work_budget", None)
                                else:
                                    projection_budget += work
                                    materialize_selection(conn, log, saved["selection"])
                                    projections[key] = ("checked", projected_input(conn))
                            except workspace.EventLogWorkspaceError:
                                projections[key] = ("unavailable", None)
                        checked, effective = projections[key]
                        result["effective_input_status"] = (
                            checked if effective is None else
                            "matches" if effective == pinned_input else "changed")
                        if effective is not None:
                            result["current_effective_input"] = effective
                        if result["status"] == "current" and result["effective_input_status"] != "matches":
                            result["status"] = ("projection_changed" if checked == "checked" else
                                                "projection_not_checked")
                    else:
                        result["effective_input_status"] = "not_recorded"
                    if "observed_execution_origin" in saved:
                        pinned = saved["observed_execution_origin"]
                        retained = log_info.get("observed_execution_snapshot")
                        matching = bool(retained and retained.get("manifest_available") is True and all(
                            retained.get(key) == pinned.get(key)
                            for key in ("snapshot_sha256", "original_case_count", "original_event_count",
                                        "business_case_identity_verified", "capture_complete")
                        ))
                        result["observed_execution_origin"] = {
                            **pinned,
                            "retained_manifest_status": "matches" if matching else "unavailable_or_changed",
                            "original_history_rechecked": False,
                            "business_outcomes_verified": False,
                        }
                    if include_selection:
                        result['selection'] = saved['selection']
                    results.append(result)
            finally:
                conn.close()
        return results
    except Exception:
        return [{'reference': ref, 'status': 'check_failed'} for ref in refs]
)
    expected_projection_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}
    case_type: str | None = Field(default=None, max_length=200)
    noise: float = Field(default=0.2, ge=0, lt=1, strict=True)
    activities: float = Field(default=1.0, gt=0, le=1, strict=True)
    cohort: dict = Field(default_factory=dict)


def record_draft(log_id, payload):
    from . import event_log_model_discovery as discovery
    try:
        request = DraftRequest.model_validate(payload)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid discovery snapshot request') from None
    return discovery.discover(log_id, request.case_type, noise=request.noise, activities=request.activities,
                              cohort=request.cohort, _record_snapshot=True,
                              _expected_model_sha256=request.expected_model_sha256)


def origins(record, *, include_selection=False):
    refs = list(dict.fromkeys(r for r in record.get('evidence_refs', []) if r.startswith(PREFIX)))
    if not refs:
        return []
    results = []
    try:
        if not workspace.database_path().exists():
            return [{'reference': ref, 'status': 'unavailable'} for ref in refs]
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                current, checked_times, checked_rows = {}, {}, 0
                for ref in refs:
                    row = conn.execute('SELECT log_id,created_at,payload FROM discovery_snapshots WHERE reference=?',
                                       [ref]).fetchone() if _REF.fullmatch(ref) else None
                    if row is None:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    log, at, raw = row
                    saved = json.loads(raw)
                    if saved.get('log_id') != log or PREFIX + digest(saved) != ref:
                        results.append({'reference': ref, 'status': 'reference_mismatch'}); continue
                    try:
                        log_info = workspace._check_log(conn, log)
                    except workspace.EventLogWorkspaceError:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    if log not in current:
                        rows = source_rows(conn, log) if len(current) < MAX_CHECKED_LOGS else MAX_SOURCE_ROWS + 1
                        if checked_rows + rows <= MAX_SOURCE_ROWS:
                            current[log] = source_digest(conn, log)
                            checked_times[log] = datetime.now(timezone.utc).isoformat()
                            checked_rows += rows
                        else:
                            current[log] = None
                    result = {key: saved[key] for key in ('log_id', 'source_content_sha256', 'selection_sha256',
                                                         'discovered_model_sha256', 'quality', 'discovery_method')}
                    result.update(reference=ref, recorded_at=at.isoformat() + 'Z',
                                  source_fingerprint_checked_at=checked_times.get(log),
                                  status=('not_checked_work_budget' if current[log] is None else
                                          'current' if current[log] == saved['source_content_sha256'] else 'source_advanced'),
                                  reviewed_graph_matches_discovery=model_digest(record['process_model']) == saved['discovered_model_sha256'],
                                  association_basis='user_selected_snapshot_reference', derivation_independently_verified=False,
                                  business_rules_verified=False, execution_authorized=False)
                    if "observed_execution_origin" in saved:
                        pinned = saved["observed_execution_origin"]
                        retained = log_info.get("observed_execution_snapshot")
                        matching = bool(retained and retained.get("manifest_available") is True and all(
                            retained.get(key) == pinned.get(key)
                            for key in ("snapshot_sha256", "original_case_count", "original_event_count",
                                        "business_case_identity_verified", "capture_complete")
                        ))
                        result["observed_execution_origin"] = {
                            **pinned,
                            "retained_manifest_status": "matches" if matching else "unavailable_or_changed",
                            "original_history_rechecked": False,
                            "business_outcomes_verified": False,
                        }
                    if include_selection:
                        result['selection'] = saved['selection']
                    results.append(result)
            finally:
                conn.close()
        return results
    except Exception:
        return [{'reference': ref, 'status': 'check_failed'} for ref in refs]
)
    case_type: str | None = Field(default=None, max_length=200)
    noise: float = Field(default=0.2, ge=0, lt=1, strict=True)
    activities: float = Field(default=1.0, gt=0, le=1, strict=True)
    cohort: dict = Field(default_factory=dict)


def record_draft(log_id, payload):
    from . import event_log_model_discovery as discovery
    try:
        request = DraftRequest.model_validate(payload)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid discovery snapshot request') from None
    return discovery.discover(log_id, request.case_type, noise=request.noise, activities=request.activities,
                              cohort=request.cohort, _record_snapshot=True,
                              _expected_model_sha256=request.expected_model_sha256)


def origins(record, *, include_selection=False):
    refs = list(dict.fromkeys(r for r in record.get('evidence_refs', []) if r.startswith(PREFIX)))
    if not refs:
        return []
    results = []
    try:
        if not workspace.database_path().exists():
            return [{'reference': ref, 'status': 'unavailable'} for ref in refs]
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                current, checked_times, checked_rows = {}, {}, 0
                for ref in refs:
                    row = conn.execute('SELECT log_id,created_at,payload FROM discovery_snapshots WHERE reference=?',
                                       [ref]).fetchone() if _REF.fullmatch(ref) else None
                    if row is None:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    log, at, raw = row
                    saved = json.loads(raw)
                    if saved.get('log_id') != log or PREFIX + digest(saved) != ref:
                        results.append({'reference': ref, 'status': 'reference_mismatch'}); continue
                    try:
                        log_info = workspace._check_log(conn, log)
                    except workspace.EventLogWorkspaceError:
                        results.append({'reference': ref, 'status': 'unavailable'}); continue
                    if log not in current:
                        rows = source_rows(conn, log) if len(current) < MAX_CHECKED_LOGS else MAX_SOURCE_ROWS + 1
                        if checked_rows + rows <= MAX_SOURCE_ROWS:
                            current[log] = source_digest(conn, log)
                            checked_times[log] = datetime.now(timezone.utc).isoformat()
                            checked_rows += rows
                        else:
                            current[log] = None
                    result = {key: saved[key] for key in ('log_id', 'source_content_sha256', 'selection_sha256',
                                                         'discovered_model_sha256', 'quality', 'discovery_method')}
                    result.update(reference=ref, recorded_at=at.isoformat() + 'Z',
                                  source_fingerprint_checked_at=checked_times.get(log),
                                  status=('not_checked_work_budget' if current[log] is None else
                                          'current' if current[log] == saved['source_content_sha256'] else 'source_advanced'),
                                  reviewed_graph_matches_discovery=model_digest(record['process_model']) == saved['discovered_model_sha256'],
                                  association_basis='user_selected_snapshot_reference', derivation_independently_verified=False,
                                  business_rules_verified=False, execution_authorized=False)
                    if "observed_execution_origin" in saved:
                        pinned = saved["observed_execution_origin"]
                        retained = log_info.get("observed_execution_snapshot")
                        matching = bool(retained and retained.get("manifest_available") is True and all(
                            retained.get(key) == pinned.get(key)
                            for key in ("snapshot_sha256", "original_case_count", "original_event_count",
                                        "business_case_identity_verified", "capture_complete")
                        ))
                        result["observed_execution_origin"] = {
                            **pinned,
                            "retained_manifest_status": "matches" if matching else "unavailable_or_changed",
                            "original_history_rechecked": False,
                            "business_outcomes_verified": False,
                        }
                    if include_selection:
                        result['selection'] = saved['selection']
                    results.append(result)
            finally:
                conn.close()
        return results
    except Exception:
        return [{'reference': ref, 'status': 'check_failed'} for ref in refs]
