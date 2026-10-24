"""Classic token-based replay on reviewed process graphs (roadmap #326 C)."""
from __future__ import annotations

import random

import pytest

from server import event_log_model_conformance as graph
from shared.process_token_replay import replay_path, replay_paths, token_fitness
from test_event_log_model_conformance import imported, local_workspace, packet, selection  # noqa: F401
from test_parallel_process_model import parallel_model
from test_process_catalog import branched_model, model


def _counts(result):
    return tuple(result[key] for key in ("produced", "consumed", "missing", "remaining"))


@pytest.mark.parametrize(("path", "counts", "missing_at", "remaining_at"), [
    (("Open", "Review", "Close"), (4, 4, 0, 0), {}, {}),
    (("Open", "Close"), (3, 3, 1, 1), {"Close": 1}, {"Review": 1}),          # skipped step
    (("Open", "Review"), (3, 3, 1, 1), {"(process end)": 1}, {"Close": 1}),  # unfinished case
    (("Review", "Open", "Close"), (4, 4, 1, 1), {"Review": 1}, {"Review": 1}),  # swapped order
    (("Open", "Review", "Review", "Close"), (5, 5, 1, 1), {"Review": 1}, {"Close": 1}),  # rework
    (("Open", "X", "Review", "Close"), (4, 5, 1, 0), {"X": 1}, {}),          # step not in the graph
])
def test_sequential_replay_matches_the_textbook_counts(path, counts, missing_at, remaining_at):
    result = replay_path(path, model())
    assert _counts(result) == counts
    assert result["missing_by_activity"] == missing_at and result["remaining_by_activity"] == remaining_at
    assert result["fitness"] == token_fitness(*counts)
    assert result["fits"] is (counts[2] == counts[3] == 0)


def test_fitness_formula():
    assert token_fitness(4, 4, 0, 0) == 1.0
    assert token_fitness(3, 3, 1, 1) == pytest.approx(2 / 3, abs=1e-6)
    assert token_fitness(0, 0, 0, 0) == 0.0


def test_exclusive_choice_gateways_are_silent():
    for path in (("Open", "Review", "Close"), ("Open", "Review", "Reject")):
        assert replay_path(path, branched_model())["fits"]
    both = replay_path(("Open", "Review", "Close", "Reject"), branched_model())
    assert _counts(both) == (6, 6, 1, 1)
    assert both["missing_by_activity"] == {"Reject": 1} and both["remaining_by_activity"] == {"(process end)": 1}


def test_parallel_branches_need_every_branch_and_accept_any_interleaving():
    for path in (("A1", "B1", "A2", "B2", "Close"), ("B1", "B2", "A1", "A2", "Close")):
        assert replay_path(path, parallel_model())["fits"], path
    partial = replay_path(("A1", "A2", "Close"), parallel_model())
    assert not partial["fits"] and partial["missing_by_activity"] == {"Close": 1}
    assert partial["remaining_by_activity"] == {"B1": 1, "All checks": 1}


def test_aggregate_weights_by_cases_and_respects_budgets():
    result = replay_paths([(("Open", "Review", "Close"), 4), (("Open", "Close"), 2)], model())
    assert result["replayed_cases"] == 6 and result["fitting_cases"] == 4
    assert result["tokens"] == {"produced": 22, "consumed": 22, "missing": 2, "remaining": 2}
    assert result["missing_tokens_by_activity"] == {"Close": 2}
    assert result["business_rules_evaluated"] is False
    assert result["may_overcount_deviations_versus_optimal_alignment"] is True
    capped = replay_paths([(("Open", "Review", "Close"), 1)] * 5, model(), max_events=7)
    assert capped["replayed_cases"] == 2 and capped["replay_truncated"] is True
    with pytest.raises(ValueError):
        replay_path(("Open",) * 5001, model())


def test_noisy_logs_stay_bounded():
    random.seed(7)
    noise = [(tuple(random.choice(["A1", "A2", "B1", "B2", "Close", "Z"]) for _ in range(300)), 1)
             for _ in range(400)]
    result = replay_paths(noise, parallel_model(), max_search_states=20_000)
    assert result["replay_truncated"] is True and 0 < result["replayed_cases"] < 400


def test_saved_graph_comparison_reports_token_replay():
    paths = ([["Open", "Review", "Close"]] * 4 + [["Open", "Close"]] * 2 + [["Review", "Open", "Close"]]
             + [["Open", "Review ", "Close"]] * 3)
    result = graph.compare(imported(paths), packet(), selection())
    replay = result["token_replay"]
    assert replay["replayed_cases"] == 10 and replay["fitting_cases"] == 4
    assert replay["tokens"] == {"produced": 35, "consumed": 38, "missing": 9, "remaining": 6}
    assert replay["token_fitness"] == pytest.approx(0.5 * (1 - 9 / 38) + 0.5 * (1 - 6 / 35), abs=1e-6)
    assert replay["unmodelled_activities"] == {"Review ": 3}
    assert replay["case_population_coverage"] == 1
    assert result["structural_match_cases"] == 4  # the alignment result is unchanged
