"""Integrated local PNML model release review, not deployment authorization.

Every enabled gate is derived by the server from the supplied exact model
bytes and explicit expectations. No externally supplied pass verdicts are
accepted. Uncertain evidence is never treated as a passing required gate.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256

from . import event_log_petri_acceptance as acceptance
from . import event_log_petri_language as language
from . import event_log_petri_mutation as mutation
from . import event_log_petri_policy as policy
from . import event_log_petri_soundness as soundness
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.petri_release_review.v1"
SPEC_FORMAT = "openworkgraph.petri_release_spec.v1"
MAX_SPEC_BYTES = 384 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
LANGUAGE_MODES = {"not_required", "equivalent", "preserve_baseline", "no_new_traces"}


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def parse_spec(spec):
    required = {"format", "baseline_sha256", "candidate_sha256", "suite", "rules", "criteria"}
    if not isinstance(spec, dict) or set(spec) != required or spec["format"] != SPEC_FORMAT:
        raise EventLogWorkspaceError("send the exact supported release spec fields")
    for key in ("baseline_sha256", "candidate_sha256"):
        value = spec[key]
        if not isinstance(value, str) or not _SHA.fullmatch(value):
            raise EventLogWorkspaceError(f"{key} must be a lowercase SHA-256 digest")
    suite, _ = acceptance.parse_suite(spec["suite"])
    rules = policy.parse_rules({"rules": spec["rules"]}) if spec["rules"] != [] else []
    criteria = spec["criteria"]
    allowed = {"language", "require_baseline_acceptance", "require_candidate_termination",
               "require_candidate_one_safe", "require_rule_activation", "mutation"}
    if not isinstance(criteria, dict) or set(criteria) - allowed:
        raise EventLogWorkspaceError("unknown release criteria")
    mode = criteria.get("language", "equivalent")
    if not isinstance(mode, str) or mode not in LANGUAGE_MODES:
        raise EventLogWorkspaceError("unknown completed-language criterion")
    normalized = {"language": mode}
    for key, default in (("require_baseline_acceptance", True),
                         ("require_candidate_termination", False),
                         ("require_candidate_one_safe", False),
                         ("require_rule_activation", True)):
        value = criteria.get(key, default)
        if type(value) is not bool:
            raise EventLogWorkspaceError(f"{key} must be boolean")
        normalized[key] = value
    mutation_spec = criteria.get("mutation")
    normalized["mutation"] = None
    if mutation_spec is not None:
        if not isinstance(mutation_spec, dict) or set(mutation_spec) != {"min_detected_basis_points", "min_mutants", "settings"}:
            raise EventLogWorkspaceError("mutation criterion requires threshold, minimum count and settings")
        threshold, count = mutation_spec["min_detected_basis_points"], mutation_spec["min_mutants"]
        if type(threshold) is not int or not 0 <= threshold <= 10000:
            raise EventLogWorkspaceError("mutation threshold must be 0–10000 basis points")
        if type(count) is not int or not 1 <= count <= mutation.MAX_MUTANTS:
            raise EventLogWorkspaceError("mutation minimum count must be 1–8")
        _, _, settings = mutation._settings({"suite": suite, "settings": mutation_spec["settings"]})
        if count > settings["max_mutants"]:
            raise EventLogWorkspaceError("mutation minimum count exceeds selected maximum")
        normalized["mutation"] = {"min_detected_basis_points": threshold, "min_mutants": count,
                                  "settings": settings}
        if not normalized["require_baseline_acceptance"]:
            raise EventLogWorkspaceError("mutation criterion requires a passing baseline suite")
    result = {"format": SPEC_FORMAT, "baseline_sha256": spec["baseline_sha256"],
              "candidate_sha256": spec["candidate_sha256"], "suite": suite, "rules": rules,
              "criteria": normalized}
    if len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode()) > MAX_SPEC_BYTES:
        raise EventLogWorkspaceError("normalized release spec exceeds the supported byte limit")
    return result


def pin(baseline: bytes, candidate: bytes, spec):
    """Normalize a proposed spec and bind it to the exact submitted bytes.

    Pinning is an input freeze, not a review, signature or approval.
    """
    if not isinstance(spec, dict) or set(spec) != {"format", "suite", "rules", "criteria"}:
        raise EventLogWorkspaceError("send the exact unpinned release spec fields")
    # Validate the models at freeze time, without running expensive exploration.
    soundness._strict_net(baseline)
    soundness._strict_net(candidate)
    normalized = parse_spec({**spec, "baseline_sha256": sha256(baseline).hexdigest(),
                             "candidate_sha256": sha256(candidate).hexdigest()})
    return {"format": "openworkgraph.petri_release_pin.v1", "spec": normalized,
            "spec_sha256": _digest(normalized)}


def _gate(key, value, *, detail=None):
    return {"id": key, "status": "passed" if value is True else "failed" if value is False else "inconclusive",
            "detail": detail}


def _policy_gate(check, activation):
    # A counterexample is a failure regardless of remaining unknowns.
    if check["violating_trace_possible"] is True or check["completed_trace_possible"] is False:
        return False
    if activation and check["activated_satisfying_trace_possible"] is False:
        return False
    if check["all_completed_traces_satisfy"] is True:
        if not activation or check["activated_satisfying_trace_possible"] is True:
            return True
    return None


def _mutation_gate(report, criteria):
    counts = report["counts"]
    selected = report.get("selected_mutants", 0)
    if not counts or selected < criteria["min_mutants"]:
        return None
    threshold = criteria["min_detected_basis_points"] * selected
    if counts["detected"] * 10000 >= threshold:
        return True
    if (counts["detected"] + counts["inconclusive"]) * 10000 < threshold:
        return False
    return None


def review(baseline: bytes, candidate: bytes, spec):
    spec = parse_spec(spec)
    if sha256(baseline).hexdigest() != spec["baseline_sha256"]:
        raise EventLogWorkspaceError("baseline bytes do not match the pinned release spec")
    if sha256(candidate).hexdigest() != spec["candidate_sha256"]:
        raise EventLogWorkspaceError("candidate bytes do not match the pinned release spec")
    criteria = spec["criteria"]
    before = soundness.analyze(baseline)
    after = soundness.analyze(candidate)
    scenarios = acceptance.review(baseline, spec["suite"], candidate)
    behavior = None
    policies = None
    mutations = None
    gates = [_gate("candidate_soundness", after["sound"], detail=after["limit"]),
             _gate("candidate_acceptance", scenarios["candidate"]["suite_passed"],
                   detail=scenarios["candidate"]["counts"])]
    # Soundness and candidate acceptance are mandatory and cannot be disabled.
    if criteria["require_baseline_acceptance"]:
        gates.append(_gate("baseline_acceptance", scenarios["baseline"]["suite_passed"],
                           detail=scenarios["baseline"]["counts"]))
    if criteria["require_candidate_termination"]:
        gates.append(_gate("candidate_guaranteed_termination", after["all_maximal_executions_complete"],
                           detail=after["limit"]))
    if criteria["require_candidate_one_safe"]:
        gates.append(_gate("candidate_one_safe", after["one_safe"], detail=after["limit"]))
    mode = criteria["language"]
    if mode != "not_required":
        behavior = language.compare(baseline, candidate)
        key = {"equivalent": "equivalent", "preserve_baseline": "candidate_preserves_baseline_language",
               "no_new_traces": "candidate_introduces_no_new_language"}[mode]
        gates.append(_gate("completed_language_" + mode, behavior[key], detail=behavior["limit"]))
    if spec["rules"]:
        policies = policy.review(baseline, {"rules": spec["rules"]}, candidate)
        joint = policies["candidate"]["joint"]
        gates.append(_gate("candidate_joint_policy", _policy_gate(joint, False), detail=joint["status"]))
        for row in policies["candidate"]["rules"]:
            gates.append(_gate(f"candidate_policy_{row['rule_index']+1}",
                               _policy_gate(row, criteria["require_rule_activation"]),
                               detail=row["status"]))
    if criteria["mutation"] is not None:
        mutation_criteria = criteria["mutation"]
        mutations = mutation.review(baseline, {"suite": spec["suite"], "settings": mutation_criteria["settings"]})
        gates.append(_gate("baseline_suite_mutation_adequacy", _mutation_gate(mutations, mutation_criteria),
                           detail={"counts": mutations["counts"],
                                   "selected_mutants": mutations.get("selected_mutants", 0),
                                   "required_minimum": mutation_criteria["min_mutants"],
                                   "required_basis_points": mutation_criteria["min_detected_basis_points"]}))
    counts = {k: sum(g["status"] == k for g in gates) for k in ("passed", "failed", "inconclusive")}
    status = "blocked" if counts["failed"] else ("inconclusive" if counts["inconclusive"] else "model_criteria_met")
    report = {"format": FORMAT, "spec": spec, "spec_sha256": _digest(spec),
              "status": status, "gates": gates, "counts": counts,
              "baseline_soundness": before, "candidate_soundness": after,
              "acceptance": scenarios, "language": behavior, "policy": policies, "mutation": mutations,
              "execution_authorized": False, "real_world_outcomes_verified": False,
              "caveat": "This report evaluates only the supplied model and declared criteria. "
                        "It does not authorize deployment, establish business policy, or verify actual agent behavior."}
    return {**report, "report_sha256": _digest(report)}


def main(argv=None):
    """Offline CLI, with JSON evidence on stdout; no provider or deployment calls."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Pin or review a PNML model release specification")
    parser.add_argument("action", choices=("pin", "review"))
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--spec", required=True)
    args = parser.parse_args(argv)
    try:
        limit = soundness.replay.MAX_BYTES
        baseline = acceptance._read_bounded(args.baseline, limit)
        candidate = acceptance._read_bounded(args.candidate, limit)
        spec = acceptance._strict_json(acceptance._read_bounded(args.spec, MAX_SPEC_BYTES))
        result = pin(baseline, candidate, spec) if args.action == "pin" else review(baseline, candidate, spec)
    except (EventLogWorkspaceError, OSError, UnicodeError, RecursionError) as exc:
        print("Release review refused: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
    if args.action == "pin":
        return 0
    return {"model_criteria_met": 0, "blocked": 1, "inconclusive": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
