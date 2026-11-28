from fractions import Fraction
from itertools import product
import json
from math import comb
import subprocess
import sys

import pytest

from shared.agent_procurement_trial_design import FORMAT, critical_wins, plan


def request(**updates):
    return {"format": FORMAT, "alpha_bps": 500, "target_power_bps": 8000,
            "comparisons": 1, "maximum_cases": 8,
            "scenarios": [{"win_bps": 6000, "loss_bps": 1000}], **updates}


@pytest.mark.parametrize("alpha,m", [(500, 1), (500, 3), (100, 9), (5000, 1)])
def test_exact_null_thresholds(alpha, m):
    for d in range(51):
        eligible = [w for w in range(d+1)
                    if Fraction(sum(comb(d, k) for k in range(w, d+1)), 2**d)
                    <= Fraction(alpha, 10000*m)]
        assert critical_wins(d, alpha, m) == min(eligible, default=None)


@pytest.mark.parametrize("win,loss", [(6000, 1000), (5000, 5000), (10000, 0), (0, 0), (0, 10000)])
def test_power_against_ordered_outcomes(win, loss):
    probabilities = (Fraction(win, 10000), Fraction(loss, 10000), Fraction(10000-win-loss, 10000))
    result = plan(request(maximum_cases=7, scenarios=[{"win_bps": win, "loss_bps": loss}]))
    for row in result["rows"]:
        n = row["cases"]
        oracle = Fraction(0)
        for outcomes in product(range(3), repeat=n):
            w, l = outcomes.count(0), outcomes.count(1)
            tail = Fraction(sum(comb(w+l, k) for k in range(w, w+l+1)), 2**(w+l))
            if tail <= Fraction(1, 20):
                weight = Fraction(1)
                for o in outcomes:
                    weight *= probabilities[o]
                oracle += weight
        power = row["powers"][0]
        assert Fraction(int(power["numerator"]), int(power["denominator"])) == oracle
        assert power["meets_target"] == (oracle >= Fraction(4, 5))


def test_joint_scenario_feasibility_and_familywise_penalty():
    scenarios = [{"win_bps": 10000, "loss_bps": 0}, {"win_bps": 9000, "loss_bps": 0}]
    result = plan(request(maximum_cases=50, scenarios=scenarios))
    assert result["minimum_cases"] == next(r["cases"] for r in result["rows"] if r["all_scenarios_meet"])
    stricter = plan(request(maximum_cases=50, scenarios=scenarios, comparisons=9))
    assert stricter["minimum_cases"] >= result["minimum_cases"]
    for a, b in zip(result["rows"], stricter["rows"]):
        assert all(int(x["numerator"]) >= int(y["numerator"]) for x, y in zip(a["powers"], b["powers"]))
    assert plan(request(scenarios=[{"win_bps": 0, "loss_bps": 0}]))["minimum_cases"] is None


@pytest.mark.parametrize("changes", [{"alpha_bps": True}, {"maximum_cases": 51}, {"comparisons": 10},
    {"target_power_bps": 0}, {"scenarios": []}, {"scenarios": [{"win_bps": 10000, "loss_bps": 1}]},
    {"scenarios": [{"win_bps": 1, "loss_bps": 1}]*2}, {"unknown": 0}])
def test_invalid_requests(changes):
    with pytest.raises(ValueError):
        plan(request(**changes))


def test_cli(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request()), encoding="utf-8")
    cmd = [sys.executable, "-m", "shared.agent_procurement_trial_design", str(path)]
    result = subprocess.run(cmd, capture_output=True, check=True, text=True)
    assert json.loads(result.stdout) == plan(request())
    for invalid in ('{"format": 1, "format": 2}', 'NaN', ' '*16385):
        path.write_text(invalid, encoding="utf-8")
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 2 and not result.stdout
