from __future__ import annotations

from server.workflow_candidates import cluster_runs, sequence_similarity


def _run(eid: str, structural: list[str], readable: list[str], family: str) -> dict:
    return {
        "execution_id": eid,
        "actor_kind": "human",
        "family_key": family,
        "started_at": "2026-10-06T08:00:00Z",
        "ended_at": "2026-10-06T08:05:00Z",
        "duration_seconds": 300,
        "structural_steps": structural,
        "semantic_steps": readable,
    }


def test_similarity_tolerates_optional_or_substituted_step_but_not_shared_terminal_action():
    assert sequence_similarity(
        ["zendesk", "jira:create", "slack"],
        ["zendesk", "slack"],
    ) >= 0.72
    assert sequence_similarity(
        ["outlook", "excel", "pricing", "save"],
        ["outlook", "acrobat", "pricing", "save"],
    ) >= 0.72
    assert sequence_similarity(
        ["gmail", "salesforce", "sheets", "gmail:send"],
        ["citrix", "excel", "outlook:send"],
    ) < 0.72


def test_five_workflow_shapes_cluster_core_paths_without_merging_unrelated_send_work():
    runs: list[dict] = []

    # Support triage: four runs create a Jira issue, two valid variants do not.
    for i in range(4):
        runs.append(_run(
            f"execution:supportcreate{i:02d}",
            ["zendesk", "jira", "jira:create", "slack"],
            ["Zendesk · Work", "Jira · Work", "Jira · Create issue", "Slack · Work"],
            "human:support",
        ))
    for i in range(2):
        runs.append(_run(
            f"execution:supportskip{i:04d}",
            ["zendesk", "jira", "slack"],
            ["Zendesk · Work", "Jira · Work", "Slack · Work"],
            "human:support",
        ))

    # Sales and clinical both end in Send but must stay separate.
    for i in range(5):
        runs.append(_run(
            f"execution:sales{i:011d}",
            ["gmail", "salesforce", "sheets", "salesforce", "gmail:send"],
            ["Gmail · Open email", "Salesforce · Work", "Google Sheets · Work", "Salesforce · Work", "Gmail · Send"],
            "human:email.compose_send",
        ))
    for i in range(3):
        runs.append(_run(
            f"execution:clinical{i:008d}",
            ["citrix", "excel", "outlook:send"],
            ["Citrix · Work", "Microsoft Excel · Work", "Outlook · Send"],
            "human:email.compose_send",
        ))

    # Finance: source document alternates between Excel and Acrobat.
    for i in range(2):
        runs.append(_run(
            f"execution:financeexcel{i:02d}",
            ["outlook", "excel", "pricing", "save"],
            ["Outlook · Work", "Microsoft Excel · Work", "Internal Pricing Tool · Work", "Internal Pricing Tool · Save"],
            "human:finance",
        ))
    for i in range(2):
        runs.append(_run(
            f"execution:financepdf{i:04d}",
            ["outlook", "acrobat", "pricing", "save"],
            ["Outlook · Work", "Adobe Acrobat · Work", "Internal Pricing Tool · Work", "Internal Pricing Tool · Save"],
            "human:finance",
        ))

    clusters = cluster_runs(runs, min_runs=2)
    counts = sorted((row["execution_count"] for row in clusters), reverse=True)
    assert counts == [6, 5, 4, 3]

    support = next(row for row in clusters if row["execution_count"] == 6)
    assert support["core_steps"] == ["Zendesk · Work", "Jira · Work", "Slack · Work"]
    variation = {row["step"]: row for row in support["observed_variations"]}
    assert variation["Jira · Create issue"]["support_runs"] == 4
    assert variation["Jira · Create issue"]["absent_in_runs"] == 2
    assert support["exact_variant_count"] == 2

    finance = next(row for row in clusters if row["execution_count"] == 4)
    assert finance["core_steps"] == [
        "Outlook · Work",
        "Internal Pricing Tool · Work",
        "Internal Pricing Tool · Save",
    ]
    finance_variations = {row["step"]: row for row in finance["observed_variations"]}
    assert finance_variations["Microsoft Excel · Work"]["support_runs"] == 2
    assert finance_variations["Adobe Acrobat · Work"]["support_runs"] == 2

    send_clusters = [
        row for row in clusters
        if "human:email.compose_send" in row["coarse_family_keys"]
    ]
    assert sorted(row["execution_count"] for row in send_clusters) == [3, 5]


def test_combined_map_counts_projected_repeats_and_execution_support_separately():
    runs = [_run(f'execution:loop{i}', ['a', 'a', 'b', 'a', 'c'], [], 'loop') for i in range(3)]
    runs += [_run(f'execution:short{i}', ['a', 'c'], [], 'loop') for i in range(2)]
    assert all('observed_transition_map' not in row for row in cluster_runs(runs, similarity_threshold=0.6))
    cluster, = cluster_runs(runs, similarity_threshold=0.6, include_transition_maps=True)
    graph = cluster['observed_transition_map']
    nodes = {row['structural_step']: row for row in graph['activities']}
    assert graph['execution_count'] == 5
    assert nodes['a']['projected_occurrence_count'] == 8
    assert nodes['a']['supporting_execution_count'] == 5
    assert graph['layout_anchor_activity_id'] == nodes['a']['id']
    edges = {(row['source'], row['target']): row for row in graph['transitions']}
    assert edges[nodes['b']['id'], nodes['a']['id']]['projected_transition_count'] == 3
    assert edges[nodes['a']['id'], nodes['c']['id']]['supporting_execution_count'] == 5
    assert (nodes['a']['id'], nodes['a']['id']) not in edges  # adjacent repeats collapse upstream
    assert graph['projected_occurrence_count'] == 16
    assert graph['projected_transition_count'] == 11
    assert graph['adjacent_repeats_collapsed'] is True
    for flag in ('canonical_event_counts', 'case_membership_verified', 'step_elapsed_times_available',
                 'combined_paths_are_observed_executions', 'execution_authorized'):
        assert graph[flag] is False


def test_combined_map_uses_all_cluster_variants_not_the_display_sample():
    prefix = [f'common{i}' for i in range(10)]
    runs = [_run(f'execution:variant{i}_{j}', prefix + [f'optional{i}'], [], 'variants')
            for i in range(13) for j in range(2)]
    cluster, = cluster_runs(runs, include_transition_maps=True)
    assert len(cluster['exact_variants']) == 12
    assert cluster['exact_variant_count'] == 13
    graph = cluster['observed_transition_map']
    assert graph['execution_count'] == 26
    assert graph['projected_occurrence_count'] == 26 * 11
    assert len(graph['activities']) == 23
    assert all(row['supporting_execution_count'] == 26 for row in graph['activities']
               if row['structural_step'].startswith('common'))


def test_dense_combined_map_marks_omissions_without_changing_global_counts():
    prefix = [f'common{i}' for i in range(10)]
    runs = [_run(f'execution:dense{i}', prefix + [f'optional{i}'], [], 'variants') for i in range(80)]
    cluster, = cluster_runs(runs, include_transition_maps=True)
    graph = cluster['observed_transition_map']
    assert len(graph['activities']) == 64
    assert graph['omitted_activity_count'] == 26
    assert graph['omitted_transition_type_count'] == 26
    assert graph['execution_count'] == 80
    assert graph['projected_occurrence_count'] == 880
    assert graph['projected_transition_count'] == 800
    assert len(graph['transitions']) <= 4096


def test_combined_map_uses_only_unambiguous_aligned_readable_labels():
    runs = [_run('execution:labels1', ['tool:opaque', 'action:save'], ['Friendly tool', 'Save'], 'labels'),
            _run('execution:labels2', ['tool:opaque', 'action:save'], ['Friendly tool', 'Save'], 'labels')]
    cluster, = cluster_runs(runs, include_transition_maps=True)
    nodes = {row['structural_step']: row for row in cluster['observed_transition_map']['activities']}
    assert nodes['tool:opaque']['display_step'] == 'Friendly tool'
    runs[1]['semantic_steps'] = ['A different label', 'Save']
    cluster, = cluster_runs(runs, include_transition_maps=True)
    nodes = {row['structural_step']: row for row in cluster['observed_transition_map']['activities']}
    assert nodes['tool:opaque']['display_step'] == 'tool:opaque'
