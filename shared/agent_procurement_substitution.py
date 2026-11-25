"""Bind a reviewed procurement decision to exact local runtime inventories.

Uses the existing R17 manifest verifier. This is a no-execution preflight,
not a second deployment store or an assertion of dependency/tool compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import StrictBool

from .agent_procurement import Strict, _load, digest
from .agent_procurement_selection import operation
from .agent_procurement_trials import _validate_plan
from .process_agent_release import compare_directories, _pinned_inventory, _pinned_spec
from .process_agent_project import build_runtime_spec
from .process_shadow_review import Hash


class ArtifactBinding(Strict):
    configuration_sha256: Hash
    manifest_sha256: Hash


class Preflight(Strict):
    expected_selection_receipt_sha256: Hash
    baseline: ArtifactBinding
    candidate: ArtifactBinding
    confirmed: StrictBool


def inspect(packet, decision_bundle, receipt, baseline_directory, candidate_directory, request):
    settings=Preflight.model_validate(request)
    if not settings.confirmed:
        raise ValueError("explicit artifact review confirmation required")
    current=operation(packet,"decide",decision_bundle)
    if (digest(receipt)!=digest(current) or
            current["receipt_sha256"]!=settings.expected_selection_receipt_sha256):
        raise ValueError("selection receipt or decision evidence changed")
    if current["rehearsal_candidate_id"] is None:
        raise ValueError("selection receipt did not nominate a rehearsal candidate")
    design,_=_validate_plan(packet,decision_bundle["discovery_plan"])
    baseline=next(c for c in design.candidates if c.candidate_id==design.baseline_candidate_id)
    candidate=current["candidate"]
    if (settings.baseline.configuration_sha256!=baseline.configuration_sha256 or
            settings.candidate.configuration_sha256!=candidate["configuration_sha256"]):
        raise ValueError("artifact binding configuration differs from selected trial configuration")
    # R17 already compares generated file inventories. Retain its exact
    # result; never execute either local artifact or inspect external files.
    delta=compare_directories(baseline_directory,candidate_directory,
                              settings.baseline.manifest_sha256,settings.candidate.manifest_sha256)
    expected=build_runtime_spec(packet)
    expected_hash=hashlib.sha256(json.dumps(expected,sort_keys=True,allow_nan=False).encode()).hexdigest()
    if expected_hash!=current["spec_sha256"]:
        raise ValueError("current runtime specification differs from the evaluated specification")
    for directory,binding in ((baseline_directory,settings.baseline),(candidate_directory,settings.candidate)):
        inventory=_pinned_inventory(directory,binding.manifest_sha256)
        actual=_pinned_spec(directory,inventory)
        # Canonical JSON rather than Python equality, which conflates booleans
        # and integers. Byte-format differences are separately visible in R17.
        actual_hash=hashlib.sha256(json.dumps(actual,sort_keys=True,allow_nan=False).encode()).hexdigest()
        if actual_hash!=expected_hash:
            raise ValueError("runtime artifact does not identify the exact evaluated specification")
    result={
        "format":"openworkgraph.procurement-substitution-preflight.v1",
        "selection_receipt_sha256":current["receipt_sha256"],
        "process_reference":current["process_reference"],
        "evaluated_spec_sha256":expected_hash,
        "baseline_candidate_id":baseline.candidate_id,
        "rehearsal_candidate_id":candidate["candidate_id"],
        "artifact_bindings":settings.model_dump(exclude={"confirmed","expected_selection_receipt_sha256"}),
        "generated_runtime_specifications_match_evaluated_specification":True,
        "generated_artifacts_verified_against_supplied_manifest_pins":True,
        "runtime_comparison":delta,
        "artifact_to_candidate_configuration_link_independently_verified":False,
        "customer_managed_files_or_dependencies_verified":False,
        "live_tool_schema_compatibility_verified":False,
        "controlled_rehearsal_completed":False,
        "independent_source_outcomes_verified":False,
        "remote_deployment_verified":False,"execution_authorized":False,
        "deployment_changed":False,
        "next_steps":[
            {"step":"review_generated_file_changes","required":delta["generated_files_changed"]},
            {"step":"verify_actual_candidate_runtime_configuration_and_dependencies","required":True},
            {"step":"inspect_current_tool_catalog_and_rehearse_exact_bindings_without_writes","required":True},
            {"step":"verify_source_state_and_outcomes_in_controlled_sandbox","required":True},
            {"step":"obtain_separate_current_rollout_authorization_and_rollback_plan","required":True},
        ],
    }
    return {**result,"preflight_sha256":digest(result)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("process-export","decision-bundle","receipt","baseline-directory","candidate-directory","request","out"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        result=inspect(_load(args.process_export),_load(args.decision_bundle),_load(args.receipt),
                       args.baseline_directory,args.candidate_directory,_load(args.request))
        from .agent_procurement_replay import _private_json
        _private_json(args.out,result)
    except (OSError,ValueError,KeyError,TypeError,RecursionError,OverflowError):
        parser.exit(2,"Substitution preflight rejected or output unavailable. No runtime was executed or deployed.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
