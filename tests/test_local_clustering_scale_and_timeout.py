"""Exact semantic parity for optimized workflow clustering at larger scale."""
from __future__ import annotations

import random

import httpx
import pytest

from server.workflow_candidates import _lcs, _lcs_length, sequence_similarity


def _legacy_lcs(a, b):
    tab = [[() for _ in range(len(b) + 1)] for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                tab[i][j] = tab[i - 1][j - 1] + (a[i - 1],)
            else:
                up, left = tab[i - 1][j], tab[i][j - 1]
                tab[i][j] = up if len(up) >= len(left) else left
    return tab[-1][-1]


def test_lcs_matches_prior_exact_subsequence_and_all_similarity_scores():
    rng = random.Random(20261014)
    pool = ("gmail", "jira", "docs", "copy", "paste", "search", "save", "excel")
    for _ in range(900):
        a = tuple(rng.choices(pool, k=rng.randrange(0, 18)))
        b = tuple(rng.choices(pool, k=rng.randrange(0, 18)))
        expected = _legacy_lcs(a, b)
        assert _lcs(a, b) == expected
        assert _lcs_length(a, b) == len(expected)
        if a and b:
            score = 0.6 * len(expected) / max(len(a), len(b))
            score += 0.4 * len(expected) / min(len(a), len(b))
            assert sequence_similarity(a, b) == round(score, 6)
        else:
            assert sequence_similarity(a, b) == 0.0


def test_mcp_timeout_is_actionable_and_omits_raw_parameters(monkeypatch):
    from mcp_server import secure_runtime
    monkeypatch.setattr(secure_runtime, "_headers", lambda: {})
    def fail(*a, **kw):
        raise httpx.ReadTimeout("simulated timeout")
    monkeypatch.setattr(httpx.Client, "get", fail)
    with pytest.raises(Exception) as exc:
        secure_runtime.secure_get("/v1/workflow-evidence/families",
                                  {"query": "private-secret-needle", "limit": 25000})
    explanation = str(exc.value)
    assert "15 seconds" in explanation
    assert "Narrow the date range" in explanation
    assert "private-secret-needle" not in explanation
