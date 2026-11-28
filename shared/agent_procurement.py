"""Vendor-neutral procurement review of exact OWG shadow evaluation cohorts.

The comparison is offline, customer-controlled and descriptive. Telemetry and
candidate labels are declarations; no provider identity, independent outcomes
or execution authorization is established.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from shared.process_shadow_review import ShadowEvaluation, inspect_shadow_report

FORMAT = "openworkgraph.agent-procurement.v1"
MAX_BYTES = 8 * 1024 * 1024
Alias = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")]
CaseId = Annotated[str, Field(pattern=r"^case_[0-9]{1,4}$")]
Cost = Annotated[StrictInt, Field(ge=0, le=10**12)]
Duration = Annotated[StrictInt, Field(ge=0, le=86_400_000)]
CRITICAL = frozenset(("invalid_or_stale_plan", "invalid_route", "unresolved_review_bypassed", "expected_clarification"))
CATEGORIES = ("normal", "exception", "missing_facts")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Measurement(Strict):
    case_id: CaseId
    cost_microunits: Cost | None
    elapsed_ms: Duration | None


class Candidate(Strict):
    candidate_id: Alias
    provider: Alias
    model: Alias
    runtime: Alias
    report: ShadowEvaluation
    measurements: list[Measurement] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def coverage(self):
        ids = [m.case_id for m in self.measurements]
        if len(ids) != len(set(ids)) or set(ids) != {c.case_id for c in self.report.cases}:
            raise ValueError("measurements must cover every shadow case exactly once; unknowns must be null")
        return self


class Gates(Strict):
    minimum_match_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)]
    maximum_critical_cases: Annotated[StrictInt, Field(ge=0, le=50)]
    maximum_total_cost_microunits: Cost | None = None
    maximum_p95_elapsed_ms: Duration | None = None
    require_all_categories: StrictBool = True
    prohibit_new_critical_cases: StrictBool = True
    prohibit_baseline_regressions: StrictBool = False


class Request(Strict):
    format: Literal["openworkgraph.agent-procurement.v1"]
    baseline_candidate_id: Alias
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    gates: Gates
    candidates: list[Candidate] = Field(min_length=2, max_length=10)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _cohort(report):
    return {c.case_id: (c.case_sha256, c.category) for c in report.cases}


def _p95(values):
    # Nearest-rank percentile, exact even with a single observation.
    return sorted(values)[(95 * len(values) + 99) // 100 - 1]


def _summary(candidate, cohort, gates):
    report = candidate.report
    n = len(cohort)
    rows = {c.case_id: c for c in report.cases}
    cost = [m.cost_microunits for m in candidate.measurements if m.cost_microunits is not None]
    elapsed = [m.elapsed_ms for m in candidate.measurements if m.elapsed_ms is not None]
    critical = {c.case_id for c in report.cases if c.reason in CRITICAL}
    cats = {cat: {"cases": sum(c.category == cat for c in report.cases),
                  "matches": sum(c.category == cat and c.matches_reviewed_expectation for c in report.cases)}
            for cat in CATEGORIES}
    missing = [cat for cat in CATEGORIES if cats[cat]["cases"] == 0]
    total_cost = sum(cost) if len(cost) == n else None
    p95 = _p95(elapsed) if len(elapsed) == n else None
    checks = {
        "match_rate": "pass" if report.matching_case_count * 10000 >= gates.minimum_match_basis_points * n else "fail",
        "critical_cases": "pass" if len(critical) <= gates.maximum_critical_cases else "fail",
        "category_coverage": "fail" if gates.require_all_categories and missing else "pass",
    }
    if gates.maximum_total_cost_microunits is not None:
        checks["total_cost"] = ("fail" if sum(cost) > gates.maximum_total_cost_microunits else
                                "unknown" if total_cost is None else "pass")
    if gates.maximum_p95_elapsed_ms is not None:
        checks["p95_latency"] = ("unknown" if p95 is None else
                                  "pass" if p95 <= gates.maximum_p95_elapsed_ms else "fail")
    return {
        "candidate_id": candidate.candidate_id, "provider": candidate.provider, "model": candidate.model,
        "runtime": candidate.runtime, "report_sha256": digest(report.model_dump()),
        "results_sha256": report.results_sha256,
        "case_count": n, "matching_cases": report.matching_case_count,
        "match_rate": report.matching_case_count / n,
        "critical_cases": len(critical), "critical_case_ids": sorted(critical),
        "categories": cats, "missing_categories": missing, "reason_counts": dict(report.reason_counts),
        "cost": {"known_cases": len(cost), "unknown_cases": n-len(cost),
                 "known_cost_lower_bound_microunits": sum(cost), "total_microunits": total_cost},
        "latency": {"known_cases": len(elapsed), "unknown_cases": n-len(elapsed), "p95_ms": p95},
        "gates": checks,
    }


def _compare(base, candidate):
    left = {r.case_id:r for r in base.report.cases}
    right = {r.case_id:r for r in candidate.report.cases}
    lm = {r.case_id:r for r in base.measurements}
    rm = {r.case_id:r for r in candidate.measurements}
    rows = []
    for case_id in sorted(left):
        a,b = left[case_id],right[case_id]
        x,y = lm[case_id],rm[case_id]
        change = ("improved" if not a.matches_reviewed_expectation and b.matches_reviewed_expectation
                  else "regressed" if a.matches_reviewed_expectation and not b.matches_reviewed_expectation
                  else "unchanged_match" if a.matches_reviewed_expectation else "unchanged_mismatch")
        rows.append({"case_id":case_id,"category":a.category,"change":change,
                     "baseline_reason":a.reason,"candidate_reason":b.reason,
                     "new_critical":a.reason not in CRITICAL and b.reason in CRITICAL,
                     "cost_delta_microunits": y.cost_microunits-x.cost_microunits
                         if x.cost_microunits is not None and y.cost_microunits is not None else None,
                     "elapsed_delta_ms": y.elapsed_ms-x.elapsed_ms
                         if x.elapsed_ms is not None and y.elapsed_ms is not None else None})
    counts = Counter(r["change"] for r in rows)
    return {"candidate_id":candidate.candidate_id,
            "counts":{k:counts[k] for k in ("improved","regressed","unchanged_match","unchanged_mismatch")},
            "new_critical_cases":sum(r["new_critical"] for r in rows),
            "cases":rows}


def _decision(checks):
    return "fail" if "fail" in checks.values() else "unknown" if "unknown" in checks.values() else "pass"


def _frontier(summaries):
    # Only complete, gate-passing candidates participate. Missing measurements
    # cannot beat a measured candidate by being assigned zero cost or latency.
    eligible = [s for s in summaries if s["gate_status"] == "pass"
                and s["cost"]["total_microunits"] is not None and s["latency"]["p95_ms"] is not None]
    def vector(s):
        return (-s["matching_cases"], s["critical_cases"], s["cost"]["total_microunits"], s["latency"]["p95_ms"])
    def dominates(a,b):
        return all(x<=y for x,y in zip(a,b)) and any(x<y for x,y in zip(a,b))
    return {"candidate_ids":sorted(s["candidate_id"] for s in eligible
              if not any(dominates(vector(t),vector(s)) for t in eligible)),
            "eligible_candidate_ids":sorted(s["candidate_id"] for s in eligible),
            "excluded_candidate_ids":sorted(s["candidate_id"] for s in summaries if s not in eligible),
            "dimensions":["maximize_matching_cases","minimize_critical_cases","minimize_total_cost","minimize_p95_latency"]}


def compare(packet, value):
    """Compare a frozen common population against the existing exact spec validator."""
    req = Request.model_validate(value)
    candidates = {c.candidate_id:c for c in req.candidates}
    if len(candidates) != len(req.candidates) or req.baseline_candidate_id not in candidates:
        raise ValueError("unique candidate IDs and an existing baseline are required")
    baseline = candidates[req.baseline_candidate_id]
    cohort = _cohort(baseline.report)
    report_hashes = set()
    shared_results = {}
    summaries, comparisons = [], []
    for candidate in req.candidates:
        report = candidate.report
        # Exact version/spec binding is resolved against the supplied saved export,
        # not just matching claims between two otherwise incompatible reports.
        inspect_shadow_report(packet, report, latest_revision=packet["revision"])
        if report.suite_sha256 != baseline.report.suite_sha256 or _cohort(report) != cohort:
            raise ValueError("candidates must use the exact same reviewed suite and complete case population")
        h = digest(report.model_dump())
        if h in report_hashes:
            raise ValueError("duplicate report evidence across candidates")
        report_hashes.add(h)
        shared_results.setdefault(report.results_sha256, []).append(candidate.candidate_id)
        s = _summary(candidate, cohort, req.gates)
        paired = _compare(baseline, candidate)
        if req.gates.prohibit_new_critical_cases:
            s["gates"]["no_new_critical_cases"] = "fail" if paired["new_critical_cases"] else "pass"
        if req.gates.prohibit_baseline_regressions:
            s["gates"]["no_baseline_regressions"] = "fail" if paired["counts"]["regressed"] else "pass"
        s["gate_status"] = _decision(s["gates"])
        summaries.append(s)
        if candidate.candidate_id != baseline.candidate_id:
            comparisons.append(paired)
    return {"format":FORMAT,"request_sha256":digest(req.model_dump()),
            "process_reference":baseline.report.process_reference.model_dump(),
            "spec_sha256":baseline.report.spec_sha256,"suite_sha256":baseline.report.suite_sha256,
            "case_population_sha256":digest(cohort),"baseline_candidate_id":baseline.candidate_id,
            "currency":req.currency,"case_count":len(cohort),"gates":req.gates.model_dump(),
            "candidates":summaries,"comparisons":comparisons,"pareto":_frontier(summaries),
            "shared_results_hashes":[{"results_sha256":h,"candidate_ids":sorted(ids)}
                                      for h,ids in sorted(shared_results.items()) if len(ids)>1],
            "candidate_identity_verified":False,"telemetry_independently_verified":False,
            "report_authenticity_verified":False,"independent_business_outcomes_verified":False,
            "representative_population_established":False,"execution_authorized":False,
            "automatic_selection_performed":False,
            "limitations":["Shadow matches are agreement with declared expectations, not verified business task success.",
                           "Cost and latency are operator-supplied measurements; use common all-in accounting and timing boundaries.",
                           "Null measurements remain unknown; Pareto review excludes incomplete telemetry.",
                           "One complete capture per candidate is compared; shared result hashes are disclosed, not independent replication.",
                           "The supplied export and report digests are content identifiers, not authenticated provenance.",
                           "No models, business tools, provider services or current source-state checks were called."]}


def _load(path):
    with path.open("rb") as handle:
        raw=handle.read(MAX_BYTES+1)
    return loads(raw)


def loads(raw):
    """Parse a bounded input without losing duplicate-key evidence."""
    if isinstance(raw,str):
        raw=raw.encode("utf-8")
    if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:
        raise ValueError("input exceeds 8 MiB or is not text")
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:
                raise ValueError("duplicate JSON key")
            out[k]=v
        return out
    def invalid(_):
        raise ValueError("nonfinite JSON number")
    return json.loads(raw,object_pairs_hook=unique,parse_constant=invalid)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-export",type=Path,required=True)
    parser.add_argument("--request",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        result=compare(_load(args.process_export),_load(args.request))
        with args.out.open("x",encoding="utf-8") as handle:
            json.dump(result,handle,indent=2,allow_nan=False)
            handle.write("\n")
    except (OSError,ValueError,KeyError,TypeError,RecursionError):
        parser.exit(2,"Invalid or incompatible procurement inputs, or output unavailable. No selection was authorized.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
