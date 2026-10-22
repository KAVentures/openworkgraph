"""Descriptive change analysis of two complete, minimized event-log snapshots.

Labels are compared literally. No identity joins, business-rule evaluation,
statistical significance, causal explanation or automatic process updates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import ceil
from statistics import median

from .process_conformance import compare_path

FORMAT = 'openworkgraph.process-log-drift.v1'
MAX_PATH_ROWS = 50
MAX_TRANSITION_ROWS = 100
MAX_DISPLAY_PATH = 24


def _share(count, total):
    return count / total if total else 0.0


def _change(before, after, before_total, after_total):
    return {
        'baseline_case_count': before, 'current_case_count': after,
        'baseline_case_fraction': round(_share(before, before_total), 6),
        'current_case_fraction': round(_share(after, after_total), 6),
        'case_share_change_percentage_points': round(100 * (
            _share(after, after_total) - _share(before, before_total)), 4),
    }


def _timing(before, after):
    a = round(median(before), 3) if before else None
    b = round(median(after), 3) if after else None
    return {
        'baseline_median_elapsed_seconds': a, 'current_median_elapsed_seconds': b,
        'median_change_seconds': round(b - a, 3) if a is not None and b is not None else None,
        # Zero is a valid baseline, never a denominator to fabricate a ratio.
        'median_change_percent': round(100 * (b - a) / a, 4) if a and b is not None else None,
    }


def _summarize(snapshot):
    paths = Counter()
    durations = defaultdict(list)
    activity_events, activity_cases = Counter(), Counter()
    edge_events, edge_cases, edge_gaps = Counter(), Counter(), defaultdict(list)
    for case in snapshot['cases']:
        path = case['path']
        paths[path] += 1
        durations[path].append(case['duration_seconds'])
        activity_events.update(path)
        activity_cases.update(set(path))
        edges = list(zip(path, path[1:]))
        edge_events.update(edges)
        edge_cases.update(set(edges))
        for edge, gap in zip(edges, case['gaps']):
            edge_gaps[edge].append(gap)
    return {
        'paths': paths, 'durations': durations, 'activity_events': activity_events,
        'activity_cases': activity_cases, 'edge_events': edge_events,
        'edge_cases': edge_cases, 'edge_gaps': edge_gaps,
        'case_count': sum(paths.values()),
    }


def compare_snapshots(baseline, current, *, reference_model=None, reference=None):
    """Inputs are store-internal full snapshots, bounded by the CSV importer.

    All aggregates use full paths, including paths hidden by preview sampling or
    display caps. Comparison rows have independent literal path fingerprints;
    per-import frequency-ranked variant_1 etc. must never be used as identity.
    """
    a, b = _summarize(baseline), _summarize(current)
    na, nb = a['case_count'], b['case_count']
    paths = set(a['paths']) | set(b['paths'])
    activities = set(a['activity_cases']) | set(b['activity_cases'])
    edges = set(a['edge_cases']) | set(b['edge_cases'])

    def ranking(key, ca, cb):
        return (-abs(_share(cb[key], nb) - _share(ca[key], na)),
                -ca[key] - cb[key], key)

    path_rows = []
    for path in sorted(paths, key=lambda p: ranking(p, a['paths'], b['paths']))[:MAX_PATH_ROWS]:
        path_rows.append({
            'path_sha256': sha256(json.dumps(list(path), ensure_ascii=False,
                separators=(',', ':')).encode('utf-8')).hexdigest(),
            'observed_path': list(path[:MAX_DISPLAY_PATH]), 'path_event_count': len(path),
            'path_truncated': len(path) > MAX_DISPLAY_PATH,
            'presence': 'current_only' if path not in a['paths'] else
                        'baseline_only' if path not in b['paths'] else 'both',
            **_change(a['paths'][path], b['paths'][path], na, nb),
            **_timing(a['durations'][path], b['durations'][path]),
        })
    activity_rows = [{
        'activity': activity,
        'baseline_event_count': a['activity_events'][activity],
        'current_event_count': b['activity_events'][activity],
        **_change(a['activity_cases'][activity], b['activity_cases'][activity], na, nb),
    } for activity in sorted(activities, key=lambda x: ranking(x, a['activity_cases'], b['activity_cases']))]
    edge_rows = [{
        'from': edge[0], 'to': edge[1],
        'baseline_transition_count': a['edge_events'][edge],
        'current_transition_count': b['edge_events'][edge],
        **_change(a['edge_cases'][edge], b['edge_cases'][edge], na, nb),
        **_timing(a['edge_gaps'][edge], b['edge_gaps'][edge]),
    } for edge in sorted(edges, key=lambda x: ranking(x, a['edge_cases'], b['edge_cases']))[:MAX_TRANSITION_ROWS]]
    all_durations = [[case['duration_seconds'] for case in s['cases']] for s in (baseline, current)]
    p90 = [round(sorted(values)[ceil(.9 * len(values)) - 1], 3) for values in all_durations]
    overlap = (baseline['event_window']['first_event_at'] <= current['event_window']['last_event_at'] and
               current['event_window']['first_event_at'] <= baseline['event_window']['last_event_at'])
    warnings = ['case_identity_not_joined', 'coverage_and_label_equivalence_not_verified',
                'elapsed_time_not_labor_or_verified_waiting', 'descriptive_not_statistical_significance']
    if overlap:
        warnings.append('observed_event_windows_overlap')
    if min(na, nb) < 20:
        warnings.append('fewer_than_twenty_cases_in_at_least_one_snapshot')
    if any(s['data_quality'].get('cases_with_tied_timestamps', 0) for s in (baseline, current)):
        warnings.append('timestamp_ties_have_unverified_order')

    def describe(snapshot, summary):
        return {**snapshot['reference'], 'event_window': snapshot['event_window'],
                'variant_count': len(summary['paths']), 'data_quality': snapshot['data_quality']}

    report = {
        'format': FORMAT, 'baseline': describe(baseline, a), 'current': describe(current, b),
        'comparison_basis': 'literal_activity_labels_and_full_ordered_paths',
        'comparability': {'user_declared_same_process_and_compatible_labels': True,
                         'independently_verified': False, 'event_windows_overlap': overlap,
                         'cases_joined_across_snapshots': False, 'duplicate_cases_ruled_out': False},
        'coverage': {'all_retained_cases_and_events_used': True,
                     'path_rows_total': len(paths), 'path_rows_returned': len(path_rows),
                     'transition_rows_total': len(edges), 'transition_rows_returned': len(edge_rows),
                     'activity_rows_total': len(activities), 'display_path_step_limit': MAX_DISPLAY_PATH,
                     'path_rows_truncated': len(paths) > MAX_PATH_ROWS,
                     'transition_rows_truncated': len(edges) > MAX_TRANSITION_ROWS},
        'summary': {
            # Total variation over all full-path case shares; 0=identical
            # observed mix, 1=disjoint mix. No threshold or drift verdict.
            'path_mix_total_variation_distance': round(.5 * sum(abs(
                _share(a['paths'][p], na) - _share(b['paths'][p], nb)) for p in paths), 6),
            'current_only_path_count': len(set(b['paths']) - set(a['paths'])),
            'baseline_only_path_count': len(set(a['paths']) - set(b['paths'])),
            'current_cases_on_current_only_paths': sum(b['paths'][p] for p in paths if p not in a['paths']),
            'baseline_p90_case_elapsed_seconds': p90[0], 'current_p90_case_elapsed_seconds': p90[1],
            'p90_method': 'nearest_rank', **_timing(*all_durations),
        },
        'paths': path_rows, 'activities': activity_rows, 'transitions': edge_rows,
        'warnings': warnings, 'statistical_significance_tested': False,
        'business_rules_evaluated': False, 'business_outcomes_verified': False,
        'process_model_updated': False, 'comparison_report_retained': False,
        'external_calls_made': False, 'execution_permissions_granted': False,
    }
    if reference_model is not None:
        # Cache full paths across both logs. Hidden display rows still count.
        comparisons = {path: compare_path(path, reference_model) for path in paths}
        matches = [sum(s['paths'][path] for path in s['paths']
                       if comparisons[path]['exact_structural_match']) for s in (a, b)]
        report['conformance'] = {
            'reference': reference,
            'baseline_exact_structural_match_cases': matches[0],
            'current_exact_structural_match_cases': matches[1],
            'baseline_structurally_deviating_cases': na - matches[0],
            'current_structurally_deviating_cases': nb - matches[1],
            'exact_match_share_change_percentage_points': round(100 * (
                _share(matches[1], nb) - _share(matches[0], na)), 4),
            'all_retained_cases_and_full_paths_compared': True,
            'rules_and_conditions_evaluated': False, 'policy_compliance_verified': False,
        }
    return report
