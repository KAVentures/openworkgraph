"""Exact semantic parity for optimized workflow clustering at larger scale."""
from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

from server.workflow_candidates import _lcs, _lcs_length, _sequence, sequence_similarity


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
        # Public scoring intentionally removes immediate duplicate steps
        # before LCS; compare against the same normalized sequences as before.
        clean_a, clean_b = _sequence(a), _sequence(b)
        normalized_expected = _legacy_lcs(clean_a, clean_b)
        if clean_a and clean_b:
            score = 0.6 * len(normalized_expected) / max(len(clean_a), len(clean_b))
            score += 0.4 * len(normalized_expected) / min(len(clean_a), len(clean_b))
            assert sequence_similarity(a, b) == round(score, 6)
        else:
            assert sequence_similarity(a, b) == 0.0


def test_mcp_timeout_is_actionable_and_omits_raw_parameters():
    # The full suite installs a history-permission wrapper on secure_get.
    # Isolate transport behavior so the permission guard is never bypassed.
    code = r'''
import httpx
from mcp_server import secure_runtime
secure_runtime._headers = lambda: {}
def fail(*args, **kwargs):
    raise httpx.ReadTimeout("simulated timeout")
httpx.Client.get = fail
try:
    secure_runtime.secure_get(
        "/v1/workflow-evidence/families",
        {"query": "private-secret-needle", "limit": 25000},
    )
    raise AssertionError("timeout did not raise")
except Exception as exc:
    explanation = str(exc)
    assert "15 seconds" in explanation
    assert "Narrow the date range" in explanation
    assert "private-secret-needle" not in explanation
'''
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
