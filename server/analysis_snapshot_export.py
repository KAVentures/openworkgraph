"""Explicit local export into the shared aggregate contract; no network or new store."""
from datetime import datetime, timezone

from shared.analysis_snapshot import Snapshot, content_digest
from . import event_log_query as analysis, event_log_workspace as workspace
from . import process_discovery_provenance as provenance


def export_snapshot(log_id, spec):
    parsed = analysis.parse(spec)
    if parsed.limit > 100:
        raise workspace.EventLogWorkspaceError('Shared snapshots support at most 100 rows; choose a narrower analysis')
    with workspace._LOCK:
        # The same lock protects run + full-source fingerprint against append/delete.
        result = analysis.run(log_id, parsed.model_dump())
        conn = workspace._connect()
        try:
            if provenance.source_rows(conn, log_id) > provenance.MAX_SOURCE_ROWS:
                raise workspace.EventLogWorkspaceError('Source exceeds the complete fingerprint budget; no partial snapshot exported')
            source_hash = provenance.source_digest(conn, log_id)
        finally:
            conn.close()
    n = len(parsed.dimensions)
    slots = {m.name:i+1 for i,m in enumerate(parsed.metrics)}
    needed = {m.of[7:] for m in parsed.measures if m.of and m.of.startswith('metric:')}
    for metric in reversed(parsed.metrics):
        if metric.name in needed and metric.kind == 'arithmetic':
            needed.update(o.name for o in (metric.left,metric.right) if o.kind == 'metric')
    columns = [{'heading': f'Group {i+1}', 'kind': 'group'} for i in range(n)] + [
        {'heading': f'Measure {i+1}', 'kind': 'number', 'unit': result['units'][label],
         'aggregation':measure.fn, 'value_basis':'case_metric' if measure.of and measure.of.startswith('metric:') else measure.of,
         'metric_slot':slots[measure.of[7:]] if measure.of and measure.of.startswith('metric:') else None}
        for i, (label,measure) in enumerate(zip(result['columns'][n:],parsed.measures))]
    maps = [{} for _ in range(n)]
    rows = []
    for row in result['rows']:
        cells = []
        for i, value in enumerate(row):
            if i < n and value is not None:
                # Fresh codes per export/column; never export group values or their mapping.
                key = str(value)
                value = maps[i].setdefault(key, f'G{i+1}_{len(maps[i])+1}')
            cells.append(value)
        rows.append(cells)
    coverage = [{'slot': i+1, 'with_value': c['cases_with_value'], 'without_value': c['cases_without_value'],
        'missing_operand': c.get('cases_with_missing_operand', 0), 'zero_divisor': c.get('cases_with_zero_divisor', 0),
        'non_finite': c.get('cases_with_non_finite_value', 0)} for i, c in enumerate(result['metrics']) if c['name'] in needed]
    return Snapshot(generated_at=datetime.now(timezone.utc), level=parsed.level, columns=columns, rows=rows,
        matching_cases=result['matching']['cases'], matching_rows=result['matching']['rows'],
        rows_truncated=result['rows_truncated'], metric_coverage=coverage,
        non_finite_results_withheld=result['non_finite_results_withheld'], source_content_sha256=source_hash,
        analysis_spec_sha256=content_digest(result['spec']),
        metric_definition_sha256=[analysis._metric_lineage(metric.model_dump(mode='json'))['definition_sha256']
                                  for metric in parsed.metrics if metric.name in needed]).model_dump(mode='json')


def check_snapshot_freshness(log_id, request):
    """Recompute lineage under the same bounded local export path.

    This is NOT proof that the supplied numeric rows were ever published or
    independently verified. Never echo case values, source rows or group labels.
    """
    from pydantic import ValidationError
    if not isinstance(request, dict) or set(request) != {"spec", "snapshot"}:
        raise workspace.EventLogWorkspaceError("freshness request needs only spec and snapshot")
    try:
        prior = Snapshot.model_validate(request["snapshot"])
    except (ValidationError, ValueError, TypeError):
        raise workspace.EventLogWorkspaceError("invalid bounded analysis snapshot") from None
    current = export_snapshot(log_id, request["spec"])
    same_source = prior.source_content_sha256 == current["source_content_sha256"]
    same_spec = prior.analysis_spec_sha256 == current["analysis_spec_sha256"]
    same_metrics = prior.metric_definition_sha256 == current["metric_definition_sha256"]
    return {
        "format": "openworkgraph.analysis-snapshot-freshness.v1",
        "source_unchanged": same_source,
        "analysis_spec_unchanged": same_spec,
        "metric_definitions_unchanged": same_metrics,
        "lineage_current": same_source and same_spec and same_metrics,
        "rows_authenticated": False,
        "source_independently_verified": False,
        "execution_authorized": False,
        "warning": "Lineage comparison does not authenticate supplied snapshot rows or authorize decisions.",
    }
