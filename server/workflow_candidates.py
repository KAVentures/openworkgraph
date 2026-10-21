from __future__ import annotations

"""Deterministic workflow-candidate projection over derived execution indexes.

Canonical observed evidence remains authoritative. These candidates exist only to
help an external AI choose execution IDs worth inspecting. Similarity never
asserts shared business intent, policy, permission, or a required procedure.
"""

from collections import Counter, defaultdict
import hashlib
from typing import Any, Iterable

DEFAULT_SIMILARITY = 0.72
MAX_STEPS = 48
MAX_EXECUTION_IDS = 25
MAX_VARIANTS = 12


def candidate_transition_map(sequences: list[tuple[str, ...]], readable_sequences: list[tuple[str, ...]]) -> dict[str, Any]:
    """All projected cluster members, independent of displayed variant samples.

    Sequences already collapse adjacent repeats and clip at MAX_STEPS. Counts
    describe that projection, never canonical events, business cases or time.
    """
    occurrences: Counter = Counter()
    support: Counter = Counter()
    transitions: Counter = Counter()
    transition_support: Counter = Counter()
    labels: dict[str, set[str]] = defaultdict(set)
    for structural, readable in zip(sequences, readable_sequences):
        # Never align unequal normalized sequences by guessed positions.
        if len(structural) == len(readable):
            for step, label in zip(structural, readable):
                labels[step].add(label)
    for sequence in sequences:
        occurrences.update(sequence)
        support.update(set(sequence))
        pairs = list(zip(sequence, sequence[1:]))
        transitions.update(pairs)
        transition_support.update(set(pairs))
    selected = sorted(sorted(occurrences, key=lambda step: (-occurrences[step], step))[:64])
    identifiers = {step: f'candidate_step_{index}' for index, step in enumerate(selected)}
    edges = [(pair, number) for pair, number in sorted(transitions.items())
             if pair[0] in identifiers and pair[1] in identifiers]
    starts = Counter(sequence[0] for sequence in sequences if sequence and sequence[0] in identifiers)
    anchor = min(starts, key=lambda step: (-starts[step], step)) if starts else next(iter(identifiers), None)
    return {
        'format': 'openworkgraph.candidate-transition-map.v1',
        'scope': 'all_cluster_projected_sequences',
        'execution_count': len(sequences),
        'projected_occurrence_count': sum(occurrences.values()),
        'projected_transition_count': sum(transitions.values()),
        'layout_anchor_activity_id': identifiers.get(anchor),
        'activities': [{'id': identifiers[step], 'structural_step': step,
                        'display_step': next(iter(labels[step])) if len(labels[step]) == 1 else step,
                        'projected_occurrence_count': occurrences[step],
                        'supporting_execution_count': support[step]} for step in selected],
        'transitions': [{'source': identifiers[pair[0]], 'target': identifiers[pair[1]],
                         'projected_transition_count': number,
                         'supporting_execution_count': transition_support[pair]} for pair, number in edges],
        'omitted_activity_count': len(occurrences) - len(selected),
        'omitted_transition_type_count': len(transitions) - len(edges),
        'maximum_projected_steps_per_execution': MAX_STEPS,
        'adjacent_repeats_collapsed': True,
        'canonical_event_counts': False,
        'case_membership_verified': False,
        'step_elapsed_times_available': False,
        'combined_paths_are_observed_executions': False,
        'execution_authorized': False,
    }


def _sequence(value: Iterable[Any] | None) -> tuple[str, ...]:
    out: list[str] = []
    for item in value or []:
        step = str(item or "").strip()
        if not step or (out and out[-1] == step):
            continue
        out.append(step)
        if len(out) >= MAX_STEPS:
            break
    return tuple(out)


def _lcs(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    """Return the same tie-stable LCS as before, without copying tuples per cell."""
    if not left or not right:
        return ()
    rows, cols = len(left) + 1, len(right) + 1
    lengths = [[0] * cols for _ in range(rows)]
    # 1: diagonal match; 2: prefer up on ties; 3: left.
    parents = [bytearray(cols) for _ in range(rows)]
    for i in range(1, rows):
        for j in range(1, cols):
            if left[i - 1] == right[j - 1]:
                lengths[i][j] = lengths[i - 1][j - 1] + 1
                parents[i][j] = 1
            elif lengths[i - 1][j] >= lengths[i][j - 1]:
                lengths[i][j] = lengths[i - 1][j]
                parents[i][j] = 2
            else:
                lengths[i][j] = lengths[i][j - 1]
                parents[i][j] = 3
    result: list[str] = []
    i, j = rows - 1, cols - 1
    while i and j:
        direction = parents[i][j]
        if direction == 1:
            result.append(left[i - 1])
            i -= 1
            j -= 1
        elif direction == 2:
            i -= 1
        else:
            j -= 1
    result.reverse()
    return tuple(result)


def _lcs_length(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Exact LCS length using O(min(len(left), len(right))) working memory."""
    if not left or not right:
        return 0
    if len(right) > len(left):
        left, right = right, left
    previous = [0] * (len(right) + 1)
    for word in left:
        current = [0] * len(previous)
        for j, other in enumerate(right, start=1):
            current[j] = (previous[j - 1] + 1 if word == other
                          else max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]


def sequence_similarity(left: Iterable[Any] | None, right: Iterable[Any] | None) -> float:
    """Order-sensitive similarity tolerant of optional/substituted steps.

    The score rewards both coverage of the longer sequence and containment of the
    shorter sequence. One optional step therefore stays close, while workflows
    sharing only a terminal action stay far apart.
    """
    a, b = _sequence(left), _sequence(right)
    if not a or not b:
        return 0.0
    common = _lcs_length(a, b)
    longer = max(len(a), len(b))
    shorter = min(len(a), len(b))
    return round((0.6 * common / longer) + (0.4 * common / shorter), 6)


def _core(sequences: list[tuple[str, ...]]) -> list[str]:
    if not sequences:
        return []
    # Start with the shortest sequence so optional additions disappear rather
    # than become accidental requirements. LCS is deterministic thereafter.
    ordered = sorted(sequences, key=lambda seq: (len(seq), seq))
    current = ordered[0]
    for sequence in ordered[1:]:
        current = _lcs(current, sequence)
        if not current:
            break
    return list(current)


def _support_rows(sequences: list[tuple[str, ...]]) -> list[dict[str, Any]]:
    total = len(sequences)
    counts: Counter[str] = Counter()
    first_positions: dict[str, list[int]] = {}
    for sequence in sequences:
        seen: set[str] = set()
        for index, step in enumerate(sequence):
            if step in seen:
                continue
            seen.add(step)
            counts[step] += 1
            first_positions.setdefault(step, []).append(index)
    rows: list[dict[str, Any]] = []
    for step, count in counts.items():
        if count >= total:
            continue
        positions = sorted(first_positions.get(step) or [0])
        median_position = positions[len(positions) // 2]
        rows.append({
            "step": step,
            "support_runs": count,
            "runs_total": total,
            "support_fraction": round(count / total, 4) if total else 0.0,
            "absent_in_runs": total - count,
            "median_zero_based_position": median_position,
            "variation_only": True,
            "interpretation": (
                "observed in some candidate runs and absent in others; absence may "
                "represent a true variant, missing capture, or noise"
            ),
        })
    rows.sort(key=lambda row: (-int(row["support_runs"]), int(row["median_zero_based_position"]), str(row["step"])))
    return rows


def _specificity(sequence: Iterable[Any] | None) -> float:
    """Structural distinctiveness; never a verified task confidence score."""
    steps = _sequence(sequence)
    tools = [step for step in steps if step.startswith(("tool:", "approval_", "error:"))]
    actions = {step for step in steps if step.startswith("action:")
               and step not in {"action:click", "action:scroll", "action:right_click"}}
    surfaces = {step for step in steps if step.startswith("surface:")
                and step not in {"surface:browser", "surface:chrome", "surface:safari",
                                 "surface:edge", "surface:firefox"}}
    if tools:
        return 0.85
    if len(actions) >= 2:
        return 0.8
    if len(actions) == 1:
        return 0.6
    if len(surfaces) >= 2:
        return 0.3
    if len(surfaces) == 1:
        return 0.15
    return 0.05


def _navigation_rank(
    core: list[str],
    members: list[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    """Bounded evidence-navigation rank, never task confidence or success.

    Prefer repeated, *ordered* multi-app work observed on separate days over
    long passive focus spans. A linked clipboard transfer is rewarded only if
    its copy and paste were actually observed in different surfaces. Durations
    and dates are derived observations, not inferred productivity.
    """
    generic = {"surface:browser", "surface:chrome", "surface:safari",
               "surface:firefox", "surface:edge"}
    ordered = [step for step in core if step.startswith("surface:") and step not in generic]
    sequence = [step for i, step in enumerate(ordered)
                if i == 0 or step != ordered[i - 1]]
    different_apps = len(set(sequence))
    transitions = sum(a != b for a, b in zip(sequence, sequence[1:]))
    days = len({str(run.get("started_at") or "")[:10] for run in members
                if str(run.get("started_at") or "")[:10]})
    durations = sorted(max(0.0, float(run.get("duration_seconds") or 0))
                       for run in members)
    median_duration = durations[len(durations) // 2] if durations else 0.0
    clipboard_runs = sum(bool(run.get("cross_surface_clipboard_observed"))
                         for run in members)
    # The first 2 surface switches count more than incidental extra tabs.
    score = (
        0.32 * min(different_apps, 4) / 4
        + 0.18 * min(transitions, 3) / 3
        + 0.15 * min(days, 3) / 3
        + 0.12 * min(len(members), 3) / 3
        + 0.11 * min(median_duration, 180) / 180
        + 0.12 * min(clipboard_runs, 2) / 2
    )
    # No surface/action evidence: never outrank observed multi-app workflow
    # solely due to the number of idle days, long duration or repetitions.
    if not core:
        score *= 0.1
    elif different_apps < 2:
        score *= 0.45
    return round(min(score, 1.0), 4), {
        "distinct_ordered_apps": different_apps,
        "observed_cross_app_transitions": transitions,
        "observed_active_days": days,
        "median_observed_duration_seconds": round(median_duration, 3),
        "runs_with_linked_cross_app_clipboard": clipboard_runs,
        "evidence_not_task_truth": True,
    }


def cluster_runs(
    runs: list[dict[str, Any]],
    *,
    min_runs: int = 2,
    similarity_threshold: float = DEFAULT_SIMILARITY,
    structural_key: str = "structural_steps",
    readable_key: str = "semantic_steps",
    limit: int = 100,
    min_cluster_quality: float = 0.0,
    rank_by_navigation: bool = False,
    include_transition_maps: bool = False,
) -> list[dict[str, Any]]:
    """Cluster exact variants conservatively, then expose core + variations.

    Exact variants are merged only when every existing variant in the candidate
    remains above the similarity threshold (complete-link over variant sequences).
    This avoids transitive chaining that could merge unrelated workflows.
    """
    exact: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    for run in runs:
        structural = _sequence(run.get(structural_key) or run.get("steps"))
        if not structural:
            continue
        actor = str(run.get("actor_kind") or "human")
        exact.setdefault((actor, structural), []).append(run)

    variants = sorted(
        exact.items(),
        key=lambda item: (-len(item[1]), item[0][0], item[0][1]),
    )

    groups: list[list[tuple[tuple[str, tuple[str, ...]], list[dict[str, Any]]]]] = []
    # Positive sequence similarity requires a shared structural step. The
    # inverted index prunes only provably impossible groups. Complete-link
    # checks, group order, exact scores and tie-breaks remain unchanged.
    token_groups: dict[tuple[str, str], set[int]] = defaultdict(set)
    for variant in variants:
        (actor, sequence), members = variant
        best_index: int | None = None
        best_score = -1.0
        if similarity_threshold <= 0:
            eligible = range(len(groups))
        else:
            eligible = sorted({
                index for step in set(sequence)
                for index in token_groups.get((actor, step), ())
            })
        for index in eligible:
            group = groups[index]
            if any(key[0] != actor for key, _members in group):
                continue
            scores = [sequence_similarity(sequence, key[1]) for key, _members in group]
            if not scores or min(scores) < float(similarity_threshold):
                continue
            score = sum(scores) / len(scores)
            if score > best_score:
                best_index, best_score = index, score
        if best_index is None:
            best_index = len(groups)
            groups.append([variant])
        else:
            groups[best_index].append(variant)
        for step in set(sequence):
            token_groups[(actor, step)].add(best_index)

    threshold = max(1, int(min_runs))
    output: list[dict[str, Any]] = []
    for group in groups:
        members = [run for _key, rows in group for run in rows]
        if len(members) < threshold:
            continue

        structural_sequences = [
            _sequence(run.get(structural_key) or run.get("steps"))
            for run in members
        ]
        readable_sequences = [
            _sequence(run.get(readable_key) or run.get(structural_key) or run.get("steps"))
            for run in members
        ]
        core_structural = _core(structural_sequences)
        core_readable = _core(readable_sequences)
        quality = _specificity(core_structural)
        if quality < float(min_cluster_quality):
            continue
        navigation_score, navigation_evidence = _navigation_rank(core_structural, members)
        actor = str(members[0].get("actor_kind") or "human")
        variant_signatures = sorted("|".join(key[1]) for key, _rows in group)
        material = (
            actor
            + "|core=" + "|".join(core_structural)
            + "|variants=" + "||".join(variant_signatures)
        )
        candidate_id = "cluster:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

        surface_labels: dict[str, str] = {}
        for run in members:
            for step in _sequence(run.get(structural_key) or run.get("steps")):
                if not step.startswith("surface:"):
                    continue
                label = (run.get("surface_labels") or {}).get(step)
                # The source adapter must provide a vetted label. Unknown or
                # hashed custom names stay opaque in public results.
                surface_labels[step] = str(label or "Other surface (redacted)")[:60]

        variant_rows: list[dict[str, Any]] = []
        for (variant_actor, structural), variant_members in sorted(
            group, key=lambda item: (-len(item[1]), item[0][1])
        ):
            readable = _sequence(
                variant_members[0].get(readable_key)
                or variant_members[0].get(structural_key)
                or variant_members[0].get("steps")
            )
            variant_rows.append({
                "structural_steps": list(structural),
                "readable_steps": list(readable),
                "execution_count": len(variant_members),
                "execution_ids": [
                    str(run.get("execution_id") or "")
                    for run in variant_members[:MAX_EXECUTION_IDS]
                    if str(run.get("execution_id") or "")
                ],
            })

        starts = [str(run.get("started_at") or "") for run in members if run.get("started_at")]
        ends = [str(run.get("ended_at") or "") for run in members if run.get("ended_at")]
        durations = sorted(float(run.get("duration_seconds") or 0.0) for run in members)
        median_duration = durations[len(durations) // 2] if durations else 0.0

        output.append({
            "candidate_cluster_id": candidate_id,
            "actor_kind": actor,
            "execution_count": len(members),
            "execution_ids": [
                str(run.get("execution_id") or "")
                for run in members[:MAX_EXECUTION_IDS]
                if str(run.get("execution_id") or "")
            ],
            "core_structural_steps": core_structural,
            "surface_labels": dict(sorted(surface_labels.items())),
            "core_steps": core_readable,
            "observed_variations": _support_rows(readable_sequences),
            "exact_variants": variant_rows[:MAX_VARIANTS],
            "exact_variant_count": len(variant_rows),
            **({'observed_transition_map': candidate_transition_map(structural_sequences, readable_sequences)}
               if include_transition_maps else {}),
            "coarse_family_keys": sorted({
                str(run.get("family_key") or "")
                for run in members
                if str(run.get("family_key") or "")
            }),
            "first_observed_at": min(starts) if starts else None,
            "last_observed_at": max(ends) if ends else None,
            "median_duration_seconds": round(median_duration, 3),
            "similarity_threshold": float(similarity_threshold),
            "derived": True,
            "authoritative": False,
            "cluster_is_business_workflow_ground_truth": False,
            "business_membership_verified": False,
            "similarity_basis": "structural_action_sequence",
            "cluster_quality": quality,
            "cluster_quality_is_task_confidence": False,
            "navigation_rank_score": navigation_score,
            "navigation_rank_evidence": navigation_evidence,
            "navigation_rank_is_task_confidence": False,
            "specificity_basis": "shared_non_generic_structural_steps_v1",
            "generic_ui_repetition_may_be_unrelated": True,
            "canonical_evidence_overrides_cluster": True,
            "use": (
                "navigation candidate only; inspect canonical evidence for selected "
                "execution_ids before inferring task meaning"
            ),
        })

    if rank_by_navigation:
        output.sort(key=lambda row: (
            -float(row.get("navigation_rank_score") or 0.0),
            -int(row["execution_count"]),
            str(row.get("candidate_cluster_id") or ""),
        ))
    else:
        output.sort(
            key=lambda row: (
                -int(row["execution_count"]),
                str(row.get("first_observed_at") or ""),
                str(row["candidate_cluster_id"]),
            )
        )
    return output[:max(1, min(int(limit), 100))]


__all__ = [
    "DEFAULT_SIMILARITY",
    "cluster_runs",
    "sequence_similarity",
]
