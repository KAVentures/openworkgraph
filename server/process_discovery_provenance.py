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


def retain(conn, log_id, notion, result, expected_model_sha256):
    if result['process_model_sha256'] != expected_model_sha256:
        raise workspace.EventLogWorkspaceError('discovery changed; discover again before recording its source snapshot')
    rows = source_rows(conn, log_id)
    if rows > MAX_SOURCE_ROWS:
        raise workspace.EventLogWorkspaceError(f'source exceeds the {MAX_SOURCE_ROWS:,}-row fingerprint budget; no partial snapshot recorded')
    selection = {'case_notion': notion, 'cohort': result['cohort'], 'settings': result['settings']}
    payload = {'format': 'openworkgraph.log-discovery-snapshot.v1', 'log_id': log_id,
               'source_content_sha256': source_digest(conn, log_id), 'selection': selection,
               'selection_sha256': digest(selection), 'discovered_model_sha256': result['process_model_sha256'],
               'quality': result['quality'], 'discovery_method': result['discovery_method'], 'source_rows': rows}
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
            'source_content_sha256': payload['source_content_sha256'], 'selection_sha256': payload['selection_sha256'],
            'graph_and_case_copies_retained': False, 'execution_authorized': False}


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_model_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
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
                        workspace._check_log(conn, log)
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
                    if include_selection:
                        result['selection'] = saved['selection']
                    results.append(result)
            finally:
                conn.close()
        return results
    except Exception:
        return [{'reference': ref, 'status': 'check_failed'} for ref in refs]
