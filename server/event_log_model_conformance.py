"""Bounded structural comparison of retained cases with a saved process graph."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from shared.process_catalog import ProcessID
from shared.process_conformance import compare_path
from shared.process_model import ProcessModel
from shared.process_parallel import compare_parallel_path
from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_TRACE_LENGTH = 300
MAX_ALIGNMENT_WORK = 200_000
MAX_VARIANTS = 1000
MAX_DISPLAY_VARIANTS = 20


class SavedGraphQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workflow_id: ProcessID
    revision: StrictInt = Field(ge=1)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cohort: dict[str, Any] = Field(default_factory=dict)
    max_variants: StrictInt = Field(default=200, ge=1, le=MAX_VARIANTS)


def compare(log_id: str, record: dict[str, Any], spec: SavedGraphQuery,
            case_type: str | None = None) -> dict[str, Any]:
    """The caller resolves and checks the exact local catalog reference."""
    if any(record.get(key) != getattr(spec, key) for key in ('workflow_id','revision','content_sha256')):
        raise workspace.EventLogWorkspaceError('saved process reference does not match the selected artifact')
    try:
        model = ProcessModel.model_validate(record['process_model'])
    except ValidationError:
        raise workspace.EventLogWorkspaceError('Saved process graph cannot be compared; review its structure') from None
    cohort = cohorts.parse_cohort(spec.cohort)
    parallel = any(node.kind == 'parallel_split' for node in model.activities)

    def run(conn):
        cohorts.restrict(conn, cohort, log_id)
        conn.execute('''CREATE TEMP TABLE graph_cases AS SELECT case_id, count(*) AS events,
            count(*) > count(DISTINCT ts) AS ordering_uncertain FROM ce GROUP BY case_id''')
        total, oversized, tied = conn.execute('''SELECT count(*), count(*) FILTER (WHERE events > ?),
            count(*) FILTER (WHERE ordering_uncertain) FROM graph_cases''', [MAX_TRACE_LENGTH]).fetchone()
        # Exclude long cases before allocating activity arrays, rather than
        # clipping them into a path that could falsely match the graph.
        rows = conn.execute('''SELECT path, count(*) AS cases, count(*) FILTER (WHERE ordering_uncertain) FROM (
            SELECT e.case_id, list(e.activity ORDER BY e.ts,e.event_id) AS path,
                bool_or(c.ordering_uncertain) AS ordering_uncertain
            FROM ce e JOIN graph_cases c ON c.case_id=e.case_id WHERE c.events <= ? GROUP BY e.case_id)
            GROUP BY path ORDER BY cases DESC,path LIMIT ?''', [MAX_TRACE_LENGTH, spec.max_variants]).fetchall()
        unknown_reasons = {'trace_length_limit': oversized,
                           'variant_limit': total - oversized - sum(n for _, n, _ in rows),
                           'alignment_work_budget': 0}
        remaining = MAX_ALIGNMENT_WORK
        matched = deviated = analyzed = examined_variants = 0
        display = []
        for source_path, count, uncertain in rows:
            path = tuple(source_path)
            result = None
            if parallel:
                result = compare_parallel_path(path, model, max_states=remaining)
                remaining -= result['alignment_state_count']
            else:
                # Legacy alignment assumes the small CSV's total-event limit.
                # Its state-space upper bound must be budgeted for large logs.
                work = (len(path) + 1) * (len(model.activities) + 1)
                if work <= remaining:
                    remaining -= work
                    result = compare_path(path, model)
            complete = result is not None and result.get('comparison_complete', True)
            if complete:
                analyzed += count
                examined_variants += 1
                if result['exact_structural_match']:
                    matched += count
                else:
                    deviated += count
            else:
                unknown_reasons['alignment_work_budget'] += count
            if len(display) < MAX_DISPLAY_VARIANTS:
                display.append({'path': list(path), 'path_length': len(path),
                    'cases': count, 'ordering_uncertain_cases': uncertain,
                    'comparison_complete': bool(complete),
                    'exact_structural_match': result['exact_structural_match'] if complete else None,
                    'minimum_activity_edit_count': result['minimum_activity_edit_count'] if complete else None,
                    'could_be_incomplete_log_prefix': result['could_be_incomplete_log_prefix'] if complete else None,
                    'alignment': result['alignment'] if complete else [],
                    'alignment_truncated': result.get('alignment_truncated', False) if complete else False,
                    'unknown_reason': None if complete else 'alignment_work_budget'})
        unknown = total - analyzed
        assert matched + deviated + unknown == total
        return {
            'process_reference': {key: record[key] for key in ('workflow_id','revision','content_sha256','title','saved_at')},
            'cohort': cohorts.describe(cohort), 'cases': total, 'analyzed_cases': analyzed,
            'structural_match_cases': matched, 'structural_deviation_cases': deviated,
            'unknown_cases': unknown, 'unknown_case_reasons': unknown_reasons,
            'analysis_coverage': analyzed/total if total else None,
            'structural_match_share_of_analyzed_cases': matched/analyzed if analyzed else None,
            'structural_match_share_of_all_cases_lower_bound': matched/total if total else None,
            'comparison_complete': unknown == 0, 'analyzed_variants': examined_variants,
            'displayed_variants': display, 'variant_results_truncated': len(rows)>len(display) or unknown_reasons['variant_limit']>0,
            'ordering_uncertain_cases': tied,
            'ordering_basis': 'timestamp_then_event_id_for_display_not_verified_business_order',
            'label_basis': 'exact_literal_activity_names_no_normalization_or_inferred_mapping',
            'business_rules_evaluated': False, 'policy_compliance_verified': False,
            'business_outcome_verified': False,
            'limits': {'max_trace_events': MAX_TRACE_LENGTH, 'max_variants': spec.max_variants},
        }
    return workspace._query(log_id, case_type, run)
