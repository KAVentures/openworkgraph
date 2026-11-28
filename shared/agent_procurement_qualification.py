"""Prospective independent-case critical failure qualification, offline only."""
from __future__ import annotations

import argparse
import hashlib
import json
from math import comb
from pathlib import Path
import re

B = 10000
CAP = 500
DESIGN = "openworkgraph.qualification-design.v1"
PLAN = "openworkgraph.qualification-plan.v1"
LEDGER = "openworkgraph.qualification-ledger.v1"
REPORT = "openworkgraph.qualification-report.v1"
HEX = re.compile(r"[0-9a-f]{64}")
ALIAS = re.compile(r"[a-zA-Z0-9_-]{1,48}")


def _fields(obj, keys):
    if type(obj) is not dict or set(obj) != set(keys.split()):
        raise ValueError("unexpected or missing fields")


def _int(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"expected integer in [{lo}, {hi}]")
    return value


def _match(value, pattern):
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _critical(n, rate, alpha, m):
    """Largest x with P_rate(X <= x) <= alpha/m; -1 if impossible."""
    den = B**n
    tail = 0
    for x in range(n + 1):
        tail += comb(n, x) * rate**x * (B-rate)**(n-x)
        if tail * B * m > alpha * den:
            return x - 1
    return n


def _prob(n, x, rate):
    return sum(comb(n, k) * rate**k * (B-rate)**(n-k)
               for k in range(x+1)), B**n


def _settings(request, *, frozen):
    _fields(request, "format alpha_bps strata")
    if request["format"] != (PLAN if frozen else DESIGN):
        raise ValueError("invalid format")
    alpha = _int(request["alpha_bps"], 1, 5000)
    strata = request["strata"]
    if type(strata) is not list or not 1 <= len(strata) <= 12:
        raise ValueError("require 1 to 12 strata")
    names, cases = set(), set()
    for s in strata:
        _fields(s, "stratum limit_bps " + ("case_hashes" if frozen else "assumed_bps cases"))
        alias = _match(s["stratum"], ALIAS)
        if alias in names:
            raise ValueError("duplicate stratum")
        names.add(alias)
        _int(s["limit_bps"], 1, B-1)
        if frozen:
            ids = s["case_hashes"]
            if type(ids) is not list or not 1 <= len(ids) <= CAP:
                raise ValueError("invalid case population")
            for cid in ids:
                _match(cid, HEX)
                if cid in cases:
                    raise ValueError("duplicate case across or within strata")
                cases.add(cid)
        else:
            _int(s["cases"], 1, CAP)
            _int(s["assumed_bps"], 0, B)
    return alpha, strata


def design(request):
    alpha, strata = _settings(request, frozen=False)
    m = len(strata)
    results = []
    for s in strata:
        n, rate = s["cases"], s["limit_bps"]
        critical = _critical(n, rate, alpha, m)
        numerator, denominator = _prob(n, critical, s["assumed_bps"])
        # Zero-failure feasibility is monotone, so binary search exact powers.
        lo, hi = 1, CAP + 1
        while lo < hi:
            mid = (lo + hi) // 2
            if (B-rate)**mid * B * m <= alpha * B**mid:
                hi = mid
            else:
                lo = mid + 1
        results.append({"stratum": s["stratum"], "cases": n,
                        "maximum_failures_to_qualify": critical if critical >= 0 else None,
                        "minimum_zero_failure_cases_within_cap": lo if lo <= CAP else None,
                        "qualification_probability": {"numerator": str(numerator),
                         "denominator": str(denominator), "bps_floor": numerator*B//denominator}})
    return {"format": "openworkgraph.qualification-design-report.v1", "assumptions": request,
            "strata": results, "per_stratum_not_joint_probability": True}


def freeze(plan):
    _settings(plan, frozen=True)
    return {"format": LEDGER, "plan_sha256": digest(plan), "outcomes": [
        {"case_hash": cid, "status": "unknown"}
        for s in plan["strata"] for cid in s["case_hashes"]]}


def review(plan, ledger, expected_plan_sha256):
    alpha, strata = _settings(plan, frozen=True)
    pin = digest(plan)
    if _match(expected_plan_sha256, HEX) != pin:
        raise ValueError("external plan pin mismatch")
    _fields(ledger, "format plan_sha256 outcomes")
    if ledger["format"] != LEDGER or ledger["plan_sha256"] != pin:
        raise ValueError("ledger does not match frozen plan")
    enrolled = {cid for s in strata for cid in s["case_hashes"]}
    outcomes = ledger["outcomes"]
    if type(outcomes) is not list or len(outcomes) != len(enrolled):
        raise ValueError("ledger must enumerate entire enrolled population")
    indexed = {}
    for row in outcomes:
        _fields(row, "case_hash status")
        cid = _match(row["case_hash"], HEX)
        if cid not in enrolled or cid in indexed or row["status"] not in (
                "failure", "no_failure", "unknown"):
            raise ValueError("invalid, duplicate or unplanned outcome")
        indexed[cid] = row["status"]
    results = []
    for s in strata:
        values = [indexed[cid] for cid in s["case_hashes"]]
        n, f, u = len(values), values.count("failure"), values.count("unknown")
        critical = _critical(n, s["limit_bps"], alpha, len(strata))
        best, worst = f <= critical, f+u <= critical
        status = ("not_qualified" if not best else "incomplete" if u else "qualified")
        results.append({"stratum": s["stratum"], "enrolled": n, "failures": f,
                        "unknown": u, "no_failure": n-f-u,
                        "maximum_failures_to_qualify": critical if critical >= 0 else None,
                        "best_case_qualifies": best, "worst_case_qualifies": worst,
                        "status": status})
    statuses = {r["status"] for r in results}
    status = ("not_qualified" if "not_qualified" in statuses else
              "incomplete" if "incomplete" in statuses else "qualified")
    return {"format": REPORT, "plan_sha256": pin, "ledger_sha256": digest(ledger),
            "status": status, "strata": results, "execution_authorized": False,
            "limitations": "Declared independent representative cases and fixed endpoint; no authenticity, chronology, safety or rollout guarantee."}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load(path):
    with Path(path).open("rb") as stream:
        data = stream.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("input exceeds 2 MB")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("design", "freeze", "review"):
        p = sub.add_parser(command)
        p.add_argument("request")
        if command == "review":
            p.add_argument("ledger")
            p.add_argument("expected_plan_sha256")
    args = parser.parse_args(argv)
    try:
        request = _load(args.request)
        if args.command == "design":
            result = design(request)
        elif args.command == "freeze":
            result = freeze(request)
        else:
            result = review(request, _load(args.ledger), args.expected_plan_sha256)
    except (ValueError, OSError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
