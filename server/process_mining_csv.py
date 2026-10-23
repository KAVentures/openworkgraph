"""Ephemeral, bounded process mining from a user-selected CSV event log.

The imported file is never written to an OWG database or transmitted to an AI.
Case identity is EXPLICITLY declared in the CSV, not inferred from proximity.
Outputs describe event logs and inter-event gaps, not verified business outcomes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from io import StringIO
import re
from statistics import median
from typing import Any

from shared.process_model import ProcessModel
from shared.process_conformance import compare_paths

FORMAT = "openworkgraph.process-mining-csv-preview.v1"
MAX_BYTES = 1024 * 1024
MAX_ROWS = 5_000
MAX_CASES = 1_000
MAX_ACTIVITIES = 64
MAX_CASE_RESULTS = 30
MAX_VARIANTS = 20
MAX_PATH_STEPS = 24
MAX_VARIANT_EXAMPLES = 5
_REQUIRED = {"case_id", "activity", "timestamp"}
_ALLOWED = _REQUIRED | {"source", "participant", "team", "origin"}
PARTICIPANT_DIMENSIONS = ("participant", "team")
_ACTIVITY = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,63}$")


class ProcessMiningCSVError(ValueError):
    """Safe parse error: message must never contain source data."""


def _timestamp(value: str) -> datetime:
    if not value or len(value) > 64:
        raise ProcessMiningCSVError("timestamp must be an offset-aware ISO-8601 value")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing timezone")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ProcessMiningCSVError("timestamp must be an offset-aware ISO-8601 value") from None


def _seconds(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds())


def parse_csv_event_records(raw_bytes: bytes, *, participant_labels_authorized=False, case_links_authorized=False):
    """Validate one bounded CSV; callers must discard native case IDs after use."""
    if not isinstance(raw_bytes, bytes) or not raw_bytes or len(raw_bytes) > MAX_BYTES:
        raise ProcessMiningCSVError("CSV must be a nonempty file of at most 1 MiB")
    try:
        body = raw_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise ProcessMiningCSVError("CSV must use UTF-8 encoding") from None
    if "\x00" in body:
        raise ProcessMiningCSVError("CSV contains an invalid control byte")
    try:
        reader = csv.DictReader(StringIO(body, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers:
            raise ProcessMiningCSVError("CSV must have a header")
        normalized = [str(h or "").strip().lower() for h in headers]
        if (len(set(normalized)) != len(normalized) or
            not _REQUIRED.issubset(normalized) or
            not set(normalized).issubset(_ALLOWED)):
            raise ProcessMiningCSVError(
                "CSV columns must be case_id,activity,timestamp with optional source or authorized participant/team labels"
            )
        if 'origin' in normalized and case_links_authorized is not True:
            raise ProcessMiningCSVError('Shared case-key imports require explicit local review authorization')
        if case_links_authorized is True and 'origin' not in normalized:
            raise ProcessMiningCSVError('A reviewed shared-key import requires source aliases')
        dimensions=tuple(key for key in PARTICIPANT_DIMENSIONS if key in normalized)
        if dimensions and participant_labels_authorized is not True:
            raise ProcessMiningCSVError('Participant/team labels require explicit local analysis authorization')
        event_rows = []
        unique_cases: set[str] = set()
        unique_activities: set[str] = set()
        # Deliberately only include declared schema fields; other exports with
        # customer data must be minimized by the user before analysis.
        for index, row in enumerate(reader, start=1):
            if index > MAX_ROWS:
                raise ProcessMiningCSVError("CSV exceeds the 5,000 event limit")
            if None in row or any(v is None for v in row.values()):
                raise ProcessMiningCSVError("CSV contains a row with missing or extra columns")
            data = dict(zip(normalized, (row[h] for h in headers)))
            case_id = str(data["case_id"]).strip()
            if case_links_authorized is True and (not re.fullmatch(r'case_key_[1-9][0-9]{0,3}',case_id)
                    or int(case_id.rsplit('_',1)[1]) > 1000):
                raise ProcessMiningCSVError('Reviewed shared case keys must use bounded import-scoped aliases')
            origin=str(data.get('origin') or '').strip()
            if case_links_authorized is True and not re.fullmatch(r'source_[1-4]',origin):
                raise ProcessMiningCSVError('Reviewed source origins must use bounded import-scoped aliases')
            activity = str(data["activity"]).strip()
            if not case_id or len(case_id) > 120:
                raise ProcessMiningCSVError("case_id must be nonempty and at most 120 characters")
            if not _ACTIVITY.fullmatch(activity):
                raise ProcessMiningCSVError(
                    "activity must be a short structural label (letters, numbers, spaces, dash, underscore)"
                )
            # This column establishes only event provenance in the input and
            # is intentionally neither echoed nor used to infer a cross-system join.
            if len(str(data.get("source") or "")) > 64:
                raise ProcessMiningCSVError("source column values must be at most 64 characters")
            timestamp = _timestamp(str(data["timestamp"]).strip())
            unique_cases.add(case_id)
            unique_activities.add(activity)
            if len(unique_cases) > MAX_CASES or len(unique_activities) > MAX_ACTIVITIES:
                raise ProcessMiningCSVError("CSV exceeds the 1,000-case or 64-activity limit")
            labels={}
            for key in dimensions:
                value=str(data.get(key) or '').strip()
                if value and (not re.fullmatch(key+r'_[1-9][0-9]{0,3}',value) or int(value.rsplit('_',1)[1])>1000):
                    raise ProcessMiningCSVError('Participant/team columns must contain bounded import-scoped labels, never native identities')
                labels[key]=value or None
            event_rows.append((case_id, activity, timestamp, index,labels.get('participant'),labels.get('team'))+
                              ((origin,) if case_links_authorized is True else ()))
    except csv.Error:
        raise ProcessMiningCSVError("CSV could not be parsed") from None
    if not event_rows:
        raise ProcessMiningCSVError("CSV has no events")

    return event_rows,dimensions


def parse_csv_events(raw_bytes: bytes) -> list[tuple[str,str,datetime,int]]:
    """Original structural-only interface; participant metadata is never implicit."""
    rows,_dimensions=parse_csv_event_records(raw_bytes)
    return [row[:4] for row in rows]


def participant_label_analysis(by_case,dimensions):
    """Supplied-label support and adjacent changes, not people or handoff proof."""
    result={'label_scope':'this_selected_log_only','dimensions':list(dimensions),
        'native_participant_identifiers_included':False,'labels_are_verified_people':False,
        'label_source':'client_declared_import_scoped_aliases','anonymization_verified':False,
        'team_membership_verified':False,'human_handoffs_verified':False,'dimensions_summary':{}}
    for key in dimensions:
        index=3 if key=='participant' else 4
        events=Counter();cases=defaultdict(set);changes=Counter();change_cases=defaultdict(set)
        missing=unknown_pairs=ties=0
        for case_id,rows in by_case.items():
            timestamp_support=Counter(row[1] for row in rows)
            for i,row in enumerate(rows):
                label=row[index]
                if label:events[label]+=1;cases[label].add(case_id)
                else:missing+=1
                if not i:continue
                previous=rows[i-1];before=previous[index]
                if not label or not before:unknown_pairs+=1
                elif label!=before:
                    if timestamp_support[row[1]]>1 or timestamp_support[previous[1]]>1:ties+=1
                    else:
                        pair=(before,label);changes[pair]+=1;change_cases[pair].add(case_id)
        ranked=sorted(changes,key=lambda pair:(-changes[pair],pair))
        result['dimensions_summary'][key]={'label_count':len(events),'missing_label_events':missing,
            'labels':[{'label':label,'event_count':events[label],'case_count':len(cases[label])} for label in sorted(events)],
            'adjacent_label_change_count':sum(changes.values()),'unknown_adjacent_pairs':unknown_pairs,
            'ambiguous_tied_timestamp_changes':ties,'change_type_count':len(changes),
            'changes':[{'from':pair[0],'to':pair[1],'change_count':changes[pair],
                        'case_count':len(change_cases[pair])} for pair in ranked[:50]],
            'changes_truncated':len(ranked)>50}
    return result


MAX_ACTIVITY_LABEL_ROWS = 20


def _activity_label_support(by_case, dimensions, correlation):
    """All selected events/full paths; missing labels remain unknown, not people."""
    columns=[(key,3 if key=='participant' else 4) for key in dimensions]
    if correlation:columns.append(('origin',5))
    if not columns:return {},{}
    global_events=defaultdict(Counter);global_cases=defaultdict(lambda:defaultdict(set))
    variant_events=defaultdict(Counter);variant_cases=defaultdict(lambda:defaultdict(set))
    for case_id,rows in by_case.items():
        path=tuple(row[0] for row in rows)
        for row in rows:
            for dimension,index in columns:
                label=row[index]
                global_key=(row[0],dimension);variant_key=(path,row[0],dimension)
                global_events[global_key][label]+=1;global_cases[global_key][label].add(case_id)
                variant_events[variant_key][label]+=1;variant_cases[variant_key][label].add(case_id)
    def summarize(events,cases):
        known=sorted((label for label in events if label is not None),key=lambda label:(-events[label],label))
        returned=known[:MAX_ACTIVITY_LABEL_ROWS]
        return {'basis':'supplied_labels_on_this_activity_events',
            'labeled_event_count':sum(events[label] for label in known),
            'missing_label_event_count':events[None], 'missing_label_case_count':len(cases.get(None,set())),
            'label_count':len(known),'rows_returned':len(returned),'rows_truncated':len(known)>len(returned),
            'labels':[{'label':label,'event_count':events[label],'case_count':len(cases[label])} for label in returned],
            'case_support_counts_are_additive':False,'identity_and_membership_verified':False,
            'effort_or_handoffs_verified':False}
    overall=defaultdict(dict);variants=defaultdict(lambda:defaultdict(dict))
    for (activity,dimension),events in global_events.items():
        overall[activity][dimension]=summarize(events,global_cases[(activity,dimension)])
    for (path,activity,dimension),events in variant_events.items():
        variants[path][activity][dimension]=summarize(events,variant_cases[(path,activity,dimension)])
    return overall,variants


def mine_csv_events(raw_bytes: bytes, *, reference_model: ProcessModel | None = None, participant_labels_authorized=False, case_links_authorized=False) -> dict[str, Any]:
    """Preview only: no retention, provider calls or source identifiers in output."""
    event_rows,dimensions = parse_csv_event_records(raw_bytes,participant_labels_authorized=participant_labels_authorized,case_links_authorized=case_links_authorized)

    by_case: dict[str, list[tuple[str, datetime, int, str | None, str | None, str | None]]] = defaultdict(list)
    for case_id, activity, stamp, line_index, participant, team, *origins in event_rows:
        by_case[case_id].append((activity, stamp, line_index,participant,team,origins[0] if origins else None))

    durations: list[float] = []
    gaps: list[float] = []
    variants: Counter[tuple[str, ...]] = Counter()
    activity_events: Counter[str] = Counter()
    activity_cases: dict[str, set[str]] = defaultdict(set)
    transition_events: Counter[tuple[str, str]] = Counter()
    transition_cases: dict[tuple[str, str], set[str]] = defaultdict(set)
    transition_gaps: dict[tuple[str, str], list[float]] = defaultdict(list)
    repeated_steps = 0
    cases: list[dict[str, Any]] = []
    timestamp_ties = 0
    repeated_case_activity_timestamps = 0
    out_of_order_cases = 0
    variant_cases: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    variant_gaps: dict[tuple[str, ...], dict[tuple[str, str], list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    # Output opaque ordinal labels only, not customer keys or hashed keys that
    # can be guessed via enumeration. The labels are ephemeral to this analysis.
    ordered_cases = sorted(by_case.items(), key=lambda item: (
        min(row[1] for row in item[1]), item[0]
    ))
    for case_index, (native_id, rows) in enumerate(ordered_cases, start=1):
        if any(a[1] > b[1] for a, b in zip(rows, rows[1:])):
            out_of_order_cases += 1
        rows.sort(key=lambda item: (item[1], item[2]))
        case_ties = sum(a[1] == b[1] for a, b in zip(rows, rows[1:]))
        timestamp_ties += case_ties
        repeated_case_activity_timestamps += sum(n - 1 for n in Counter(
            (activity, stamp) for activity, stamp, *_ in rows
        ).values())
        path = tuple(item[0] for item in rows)
        duration = _seconds(rows[0][1], rows[-1][1])
        durations.append(duration)
        variants[path] += 1
        visited_activities: set[str] = set()
        for i, activity in enumerate(path):
            activity_events[activity] += 1
            activity_cases[activity].add(native_id)
            if activity in visited_activities:
                repeated_steps += 1
            visited_activities.add(activity)
            if i:
                edge = (path[i - 1], activity)
                transition_events[edge] += 1
                transition_cases[edge].add(native_id)
                gap_seconds = _seconds(rows[i - 1][1], rows[i][1])
                gaps.append(gap_seconds)
                transition_gaps[edge].append(gap_seconds)
                variant_gaps[path][edge].append(gap_seconds)
        cases.append({
            "case": f"Case {case_index}",
            **({'case_key_alias':native_id,'origins':sorted({row[5] for row in rows})} if case_links_authorized is True else {}),
            **{key+'_labels':sorted({row[3 if key=='participant' else 4] for row in rows if row[3 if key=='participant' else 4]}) for key in dimensions},
            "event_count": len(rows),
            "tied_timestamp_adjacent_pairs": case_ties,
            "ordering_basis": "timestamp_then_supplied_row_order",
            "tied_timestamp_order_verified": False,
            "duration_seconds": round(duration, 3),
            "repeated_activity_count": sum(
                count - 1 for count in Counter(path).values() if count > 1
            ),
            "first_event_at": rows[0][1].isoformat(),
            "last_event_at": rows[-1][1].isoformat(),
            "path": list(path[:MAX_PATH_STEPS]),
            "path_truncated": len(path) > MAX_PATH_STEPS,
            "case_end_is_business_completion": False,
            "events": [{
                "position": i + 1,
                "activity": activity,
                **({"origin":origins[0]} if case_links_authorized is True else {}),
                **{key:(participant if key=='participant' else team) for key in dimensions},
                "observed_at": stamp.isoformat(),
                "elapsed_since_previous_seconds": (
                    round(_seconds(rows[i - 1][1], stamp), 3) if i else None
                ),
            } for i, (activity, stamp, _, participant, team, *origins) in enumerate(rows[:MAX_PATH_STEPS])],
        })
        variant_cases[path].append(cases[-1])

    label_support,variant_label_support=_activity_label_support(by_case,dimensions,case_links_authorized is True)

    node_rows = [{
        "activity": activity, "event_count": n,
        "case_count": len(activity_cases[activity]),
        **({"label_support":label_support[activity]} if activity in label_support else {}),
    } for activity, n in activity_events.items()]
    node_rows.sort(key=lambda row: (-row["event_count"], row["activity"]))
    edge_rows = [{
        "from": source, "to": target, "transition_count": n,
        "case_count": len(transition_cases[(source, target)]),
        "median_elapsed_gap_seconds": round(median(transition_gaps[(source, target)]), 3),
        "max_elapsed_gap_seconds": round(max(transition_gaps[(source, target)]), 3),
        "gap_is_verified_waiting": False,
        "edge_is_causal_dependency": False,
    } for (source, target), n in transition_events.items()]
    edge_rows.sort(key=lambda row: (-row["transition_count"], row["from"], row["to"]))

    # Time-gap and repetition signals identify where a person should inspect
    # the source process. They are NOT automation, ROI or waiting-time claims.
    gap_signals = sorted(
        (row for row in edge_rows if row["median_elapsed_gap_seconds"] > 0),
        key=lambda row: (-row["median_elapsed_gap_seconds"], -row["case_count"],
                         row["from"], row["to"]),
    )[:10]
    repetition_signals = sorted(
        (row for row in node_rows if row["event_count"] > row["case_count"]),
        key=lambda row: (-(row["event_count"] - row["case_count"]), row["activity"]),
    )[:10]
    review_signals = [
        {"type": "long_observed_transition_gap", "from": row["from"], "to": row["to"],
         "observed_case_count": row["case_count"],
         "median_elapsed_gap_seconds": row["median_elapsed_gap_seconds"],
         "not_verified_wait_or_bottleneck": True}
        for row in gap_signals
    ] + [
        {"type": "repeated_activity", "activity": row["activity"],
         "observed_case_count": row["case_count"],
         "additional_observed_events": row["event_count"] - row["case_count"],
         "not_verified_rework_or_automation_opportunity": True}
        for row in repetition_signals
    ]

    variant_ids = {path: f"variant_{index}" for index, (path, _) in enumerate(
        variants.most_common(), start=1
    )}
    for path, examples in variant_cases.items():
        for example in examples:
            example["variant_id"] = variant_ids[path]
    variant_rows = [{
        "variant_id": variant_ids[path],
        "observed_path": list(path[:MAX_PATH_STEPS]),
        "path_truncated": len(path) > MAX_PATH_STEPS,
        "path_event_count": len(path),
        "case_count": count,
        "fraction_of_cases": round(count / len(cases), 4),
        # Counts and gap summaries use ALL cases in the exact full path, never
        # the returned sample or truncated display prefix.
        "activities": [{
            "activity": activity, "event_count": occurrences * count,
            "case_count": count,
            **({"label_support":variant_label_support[path][activity]} if path in variant_label_support else {}),
        } for activity, occurrences in Counter(path).items()],
        "transitions": [{
            "from": edge[0], "to": edge[1],
            "transition_count": len(values), "case_count": count,
            "median_elapsed_gap_seconds": round(median(values), 3),
            "max_elapsed_gap_seconds": round(max(values), 3),
            "gap_is_verified_waiting": False,
            "edge_is_causal_dependency": False,
        } for edge, values in variant_gaps[path].items()],
        "median_case_elapsed_seconds": round(median(
            case["duration_seconds"] for case in variant_cases[path]
        ), 3),
        "ambiguous_order_case_count": sum(case['tied_timestamp_adjacent_pairs'] > 0 for case in variant_cases[path]),
        "case_examples": variant_cases[path][:MAX_VARIANT_EXAMPLES],
        "case_examples_truncated": count > MAX_VARIANT_EXAMPLES,
    } for path, count in variants.most_common(MAX_VARIANTS)]

    result = {
        "format": FORMAT,
        "source": "explicit_user_selected_csv_event_log",
        "input_rows": len(event_rows),
        "case_count": len(cases),
        "activity_count": len(activity_events),
        "transition_type_count": len(edge_rows),
        "variant_count": len(variants),
        "metrics": {
            "median_case_elapsed_seconds": round(median(durations), 3),
            "median_inter_event_gap_seconds": round(median(gaps), 3) if gaps else None,
            "repeated_activity_events": repeated_steps,
            "inter_event_gaps_are_not_verified_waiting_time": True,
            "repeated_activities_are_not_verified_rework": True,
            "elapsed_time_is_not_active_work_time": True,
        },
        "data_quality": {
            "cases_with_tied_timestamps": sum(case['tied_timestamp_adjacent_pairs'] > 0 for case in cases),
            "tied_timestamp_adjacent_pairs": timestamp_ties,
            "same_case_activity_timestamp_repeats": repeated_case_activity_timestamps,
            "cases_sorted_from_nonchronological_input": out_of_order_cases,
            "timestamp_ties_resolved_by_supplied_row_order": True,
            "repeated_rows_are_verified_duplicate_events": False,
            "source_log_completeness_verified": False,
            "source_identity_authoritativeness_verified": False,
        },
        "activities": node_rows,
        "transitions": edge_rows,
        "review_signals": review_signals,
        "review_signals_are_confirmed_bottlenecks": False,
        "estimated_labor_savings_from_timestamps": None,
        "variants": variant_rows,
        "cases": cases[:MAX_CASE_RESULTS],
        "cases_truncated": len(cases) > MAX_CASE_RESULTS,
        "variants_truncated": len(variants) > MAX_VARIANTS,
        "import_retained": False,
        "output_contains_native_case_identifiers": False,
        "case_identity_basis": "explicit_user_supplied_case_id_only",
        "cross_app_identity_inferred": False,
        "business_outcomes_verified": False,
        "agent_permissions_granted": False,
        "authoritative": False,
    }

    if case_links_authorized is True:
        source_events=Counter(row[5] for rows in by_case.values() for row in rows)
        source_cases=Counter(origin for rows in by_case.values() for origin in {row[5] for row in rows})
        result['case_correlation']={'scope':'this_selected_log_only',
            'join_basis':'caller_reviewed_literal_shared_case_key',
            'alias_source':'client_declared_import_scoped_aliases','anonymization_verified':False,
            'authoritative_case_namespace_confirmed_by_caller':True,
            'case_identity_independently_verified':False,'source_origins_independently_verified':False,
            'native_case_keys_included':False,'native_source_identifiers_included':False,
            'source_count':len(source_events),
            'multi_source_case_count':sum(len({row[5] for row in rows})>1 for rows in by_case.values()),
            'sources':[{'origin':origin,'event_count':source_events[origin],'case_count':source_cases[origin]}
                       for origin in sorted(source_events)]}
        result['case_identity_basis']='caller_reviewed_literal_shared_case_key'

    if dimensions:
        result['participant_labels']=participant_label_analysis(by_case,dimensions)

    if reference_model is not None:
        comparisons = compare_paths(variants,reference_model)
        for item, (path, _) in zip(variant_rows, variants.most_common(MAX_VARIANTS)):
            item['conformance'] = comparisons[path]
        matched = sum(n for path, n in variants.items() if comparisons[path]['exact_structural_match'])
        possible_prefixes = sum(n for path, n in variants.items() if comparisons[path]['could_be_incomplete_log_prefix'])
        unknown = sum(n for path,n in variants.items() if comparisons[path]['exact_structural_match'] is None)
        result['conformance'] = {
            'case_count': len(cases), 'exact_structural_match_cases': matched,
            'structurally_deviating_cases': len(cases) - matched - unknown,
            'possible_incomplete_prefix_cases': possible_prefixes,
            'comparison_covers_all_cases_and_variants': not unknown,
            **({'structural_comparison_unknown_cases':unknown,'all_cases_attempted':True}
               if any('comparison_complete' in value for value in comparisons.values()) else {}),
            'matching_cases_with_ambiguous_timestamp_order': sum(
                case['tied_timestamp_adjacent_pairs'] > 0
                for path in variants if comparisons[path]['exact_structural_match']
                for case in variant_cases[path]
            ),
            'business_rules_evaluated': False, 'policy_compliance_verified': False,
            'source_log_completeness_verified': False,
        }
    return result


__all__ = ["mine_csv_events", "ProcessMiningCSVError", "MAX_BYTES"]
