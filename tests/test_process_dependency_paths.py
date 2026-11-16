"""Explain why a declared process appears in change-impact results.

The path is only a bounded chain of human declarations, never inferred
causality, source verification or business-action authorization.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from shared.process_dependencies import (
    MAX_EXPLAINED_PATHS, MAX_EXPLANATION_HOPS, impact, topology,
)


def d(root, *prerequisites, relationship="uses_output"):
    return {
        "root_process": {"workflow_id": root},
        "links": [
            {
                "id": "to_" + prerequisite,
                "prerequisite": {"workflow_id": prerequisite},
                "relationship": relationship,
                "reason": "Potentially sensitive human explanation",
            }
            for prerequisite in prerequisites
        ],
    }


def _paths(response, direction):
    return response["declared_paths"][direction]


def test_shortest_diamond_path_is_deterministic_and_link_direction_is_exact():
    originals = [d("A", "C", "B"), d("B", "D"), d("C", "D"), d("D"), d("E", "B")]
    first = impact(topology(originals), "D")
    second = impact(topology(list(reversed(originals))), "D")
    assert first == second
    assert first["potential_consumers"] == [
        {"workflow_id": "B", "distance": 1},
        {"workflow_id": "C", "distance": 1},
        {"workflow_id": "A", "distance": 2},
        {"workflow_id": "E", "distance": 2},
    ]
    routes = {x["workflow_id"]: x for x in _paths(first, "potential_consumers")["paths"]}
    # B wins the equally short A -> (B or C) -> D route by stable ID.
    assert routes["A"]["nodes_from_focus"] == ["D", "B", "A"]
    assert routes["A"]["declared_links_from_focus"] == [
        {"consumer": "B", "prerequisite": "D", "link_id": "to_D", "relationship": "uses_output"},
        {"consumer": "A", "prerequisite": "B", "link_id": "to_B", "relationship": "uses_output"},
    ]
    assert _paths(first, "potential_consumers")["complete"]
    assert first["causal_impact_verified"] is False
    assert first["declared_paths"]["business_causality_verified"] is False
    assert first["declared_paths"]["permissions_granted"] is False


def test_forward_paths_follow_consumer_to_prerequisite_and_hide_reason_text():
    graph = topology([d("A", "B", relationship="uses_policy"), d("B", "C"), d("C")])
    graph["edges"][0]["reason"] = "SECRET CUSTOMER DATA MUST NOT BE ECHOED"
    result = impact(graph, "A")
    assert result["potential_consumers"] == []
    assert [row["workflow_id"] for row in result["prerequisites"]] == ["B", "C"]
    paths = {row["workflow_id"]: row for row in _paths(result, "prerequisites")["paths"]}
    assert paths["C"]["nodes_from_focus"] == ["A", "B", "C"]
    assert paths["C"]["declared_links_from_focus"][0]["relationship"] == "uses_policy"
    assert "SECRET CUSTOMER" not in str(result)
    assert "Potentially sensitive" not in str(result)


def test_explanation_count_limit_does_not_prune_the_affected_processes():
    graph = topology([d("C" + str(i).zfill(2), "ROOT") for i in range(40)])
    result = impact(graph, "ROOT")
    paths = _paths(result, "potential_consumers")
    assert len(result["potential_consumers"]) == 40
    assert paths["matched_processes"] == 40
    assert paths["explained_processes"] == MAX_EXPLAINED_PATHS
    assert paths["withheld_for_path_limit"] == 40 - MAX_EXPLAINED_PATHS
    assert paths["withheld_for_hop_limit"] == 0
    assert paths["complete"] is False
    assert result["execution_authorized"] is False


def test_deep_paths_withheld_without_losing_reachability():
    chain = [d("P" + str(i), "P" + str(i + 1)) for i in range(15)] + [d("P15")]
    result = impact(topology(chain), "P0")
    paths = _paths(result, "prerequisites")
    assert len(result["prerequisites"]) == 15
    assert paths["explained_processes"] == MAX_EXPLANATION_HOPS
    assert paths["withheld_for_hop_limit"] == 15 - MAX_EXPLANATION_HOPS
    assert paths["withheld_for_path_limit"] == 0
    assert max(x["distance"] for x in paths["paths"]) == MAX_EXPLANATION_HOPS
    assert any(x["workflow_id"] == "P15" and x["distance"] == 15 for x in result["prerequisites"])


def test_impact_for_disconnected_and_invalid_focus():
    result = impact(topology([d("A"), d("B")]), "A")
    assert result["prerequisites"] == result["potential_consumers"] == []
    assert _paths(result, "prerequisites")["complete"]
    assert _paths(result, "potential_consumers")["complete"]
    assert _paths(result, "prerequisites")["paths"] == []
    with pytest.raises(ValueError, match="Choose a process"):
        impact(topology([d("A")]), "outside")
