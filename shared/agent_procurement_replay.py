"""Portable blind planner replay protocol for external candidate agents (R26).

This CLI never launches a candidate agent, transmits a case or calls business
tools. It runs the existing trusted generated *offline evaluator* in an
isolated child interpreter to validate the suite and recompute raw plan scores.
Customers run candidates separately in their own no-write sandbox.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any, Annotated, Literal
from zipfile import ZipFile

from pydantic import Field, StrictInt, model_validator

from .agent_procurement import Alias, Strict, _cohort, _load, digest, loads, MAX_BYTES
from .agent_procurement_trials import (Attempt, Design, MAX_REPEATS, _validate_plan,
    freeze, review, CAPTURES)
from .process_agent_project import build_runtime_project, build_runtime_spec
from .process_shadow_review import Hash, ShadowEvaluation

CHALLENGE = "openworkgraph.procurement-replay-challenge.v1"
RAW_CAPTURES = "openworkgraph.procurement-replay-raw-captures.v1"
MAX_PLAN_BYTES = 128 * 1024
MAX_SUITE_BYTES = 256 * 1024

# Fixed code only; never execute a supplied program, model output or ZIP.
# The generated project's trusted runtime/evaluate modules are identical to
# those used by the existing customer shadow evaluator. No model mode.
_OFFLINE = """
import json,sys
sys.path.insert(0,sys.argv[1])
from evaluate import compare,validate_suite
from execute import digest,reference
from runtime import load_spec
spec=load_spec()
payload=json.load(sys.stdin)
suite=payload['suite']
cases=validate_suite(spec,suite)
def evaluate(plans):
    return compare(spec,suite,{
        'format':'openworkgraph.shadow-plan-results.v1',
        'process_reference':reference(spec),'spec_sha256':digest(spec),
        'suite_sha256':digest(suite),
        'results':[{'case_id':case['id'],'plan':plans.get(case['id'])} for case in cases]})
print(json.dumps({'reference':evaluate({}),
                  'reports':[evaluate(plans) for plans in payload['captures']]},
                 allow_nan=False,separators=(',',':')))
"""


class RawAttempt(Attempt):
    slot_id: Hash
    plan: Any = None

    @model_validator(mode="after")
    def plan_only_when_delivered(self):
        present="plan" in self.model_fields_set
        if present != (self.status=="completed"):
            raise ValueError("only completed attempts must include their returned plan field")
        if present and len(json.dumps(self.plan,allow_nan=False,separators=(",",":")).encode())>MAX_PLAN_BYTES:
            raise ValueError("returned plan exceeds the supported size")
        return self


class RawCapture(Strict):
    candidate_id: Alias
    repeat: Annotated[StrictInt, Field(ge=1,le=MAX_REPEATS)]
    configuration_sha256: Hash
    attempts: list[RawAttempt] = Field(max_length=50)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({a.case_id for a in self.attempts})!=len(self.attempts):
            raise ValueError("duplicate raw attempt")
        return self


class RawLedger(Strict):
    format: Literal["openworkgraph.procurement-replay-raw-captures.v1"]
    plan_sha256: Hash
    captures: list[RawCapture] = Field(max_length=100)


def _evaluate(packet,suite,captures):
    """Score with the exact existing offline validator; data passes via stdin.

    Child interpreter has no candidate subprocess, provider configuration
    lookup, model mode or network invocation. The only extracted archive is
    constructed by the installed trusted exporter, never by the caller.
    """
    keys={"format","process_reference","spec_sha256","reviewed_by_customer","case_inputs_authorized","cases"}
    if not isinstance(suite,dict) or set(suite)!=keys:
        raise ValueError("use the exact reviewed shadow suite")
    encoded_suite=json.dumps(suite,allow_nan=False,separators=(",",":")).encode()
    if len(encoded_suite)>MAX_SUITE_BYTES:
        raise ValueError("reviewed suite exceeds 256 KiB")
    raw=json.dumps({"suite":suite,"captures":captures},allow_nan=False,separators=(",",":")).encode()
    if len(raw)>MAX_BYTES:
        raise ValueError("replay capture input exceeds the supported limit")
    with TemporaryDirectory(prefix="owg-offline-replay-") as directory:
        # The exporter generates fixed file paths and fixed Python templates.
        with ZipFile(BytesIO(build_runtime_project(packet))) as archive:
            archive.extractall(directory)
        # Do not pass API credentials, tokens or provider settings. Windows
        # needs SystemRoot/WINDIR for a child process; no shell is involved.
        env={k:os.environ[k] for k in ("SystemRoot","WINDIR","TEMP","TMP","TMPDIR") if k in os.environ}
        try:
            result=subprocess.run([sys.executable,"-I","-B","-c",_OFFLINE,directory],
                input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=directory,env=env,timeout=120)
        except (OSError,subprocess.TimeoutExpired):
            raise ValueError("offline evaluator could not complete") from None
        if result.returncode!=0 or len(result.stdout)>MAX_BYTES:
            # Never echo model text, raw inputs, stderr or provider details.
            raise ValueError("offline evaluator rejected the selected suite or plans") from None
        value=loads(result.stdout)
        reference=ShadowEvaluation.model_validate(value["reference"])
        reports=[ShadowEvaluation.model_validate(r) for r in value["reports"]]
        if len(reports)!=len(captures):
            raise ValueError("offline evaluator returned incomplete capture coverage")
        return reference,reports


def prepare(packet,suite,design,*,confirm_export_inputs=False):
    """Return frozen minimized plan and separate candidate-facing challenge."""
    if confirm_export_inputs is not True:
        raise ValueError("explicit confirmation is required to create a local case-input challenge")
    if not isinstance(design,dict) or "reference_report" in design:
        raise ValueError("prepare derives the reference report; do not supply a claimed report")
    reference,_=_evaluate(packet,suite,[])
    plan=freeze(packet,{**design,"reference_report":reference.model_dump()})
    spec=build_runtime_spec(packet)
    cases={r.case_id:r.case_sha256 for r in reference.cases}
    # The challenge intentionally contains no expected routes, review labels,
    # category, reference report, result scores, gates or suite answer file.
    challenge={
        "format":CHALLENGE,"plan_sha256":plan["plan_sha256"],
        "spec_sha256":reference.spec_sha256,"suite_sha256":reference.suite_sha256,
        "process_spec":spec,
        "cases":[{"case_id":c["id"],"input":c["input"],"input_sha256":cases[c["id"]]} for c in suite["cases"]],
        "candidates":[{k:c[k] for k in ("candidate_id","provider","model","runtime","configuration_sha256")}
                      for c in plan["basis"]["candidates"]],
        "slots":plan["slots"],"execution_authorized":False,
        "business_tools_authorized":False,"expected_case_answers_included":False,
        "candidate_output_contract":{
            "format":RAW_CAPTURES,
            "plan_fields":["revision","content_sha256","status","activity_ids","missing_business_inputs"],
            "statuses":["completed","timeout","transport_failed","invalid_output","withdrawn"],
            "note":"A proposed route or clarification is a shadow plan only, never a business action."
        }
    }
    return plan,challenge


def score(packet,suite,plan,raw):
    """Recompute reports from raw returned plans; do not trust supplied scores."""
    design,verified_plan=_validate_plan(packet,plan)
    ledger=RawLedger.model_validate(raw)
    if ledger.plan_sha256!=verified_plan["plan_sha256"]:
        raise ValueError("raw captures do not identify the frozen trial")
    candidates={c.candidate_id:c for c in design.candidates}
    slots={(r["candidate_id"],r["repeat"],r["case_id"]):r["slot_id"] for r in verified_plan["slots"]}
    seen=set(); delivered=[]; captures=[]
    for capture in ledger.captures:
        key=(capture.candidate_id,capture.repeat)
        if key in seen or capture.candidate_id not in candidates or capture.repeat>design.repeats:
            raise ValueError("duplicate or unscheduled candidate capture")
        seen.add(key)
        if capture.configuration_sha256!=candidates[capture.candidate_id].configuration_sha256:
            raise ValueError("candidate configuration differs from frozen design")
        for attempt in capture.attempts:
            if slots.get((*key,attempt.case_id))!=attempt.slot_id:
                raise ValueError("raw attempt does not identify its exact frozen slot")
        plans={a.case_id:a.plan for a in capture.attempts if a.status=="completed"}
        if plans:
            delivered.append(plans)
        captures.append((capture,bool(plans)))
    reference,reports=_evaluate(packet,suite,delivered)
    if (reference.spec_sha256!=design.reference_report.spec_sha256 or
            reference.suite_sha256!=design.reference_report.suite_sha256 or
            _cohort(reference)!=_cohort(design.reference_report)):
        raise ValueError("reviewed suite differs from the frozen exact population")
    report_iter=iter(reports)
    minimized=[]
    for capture,has_delivered in captures:
        report=next(report_iter).model_dump() if has_delivered else None
        minimized.append({
            "candidate_id":capture.candidate_id,"repeat":capture.repeat,
            "configuration_sha256":capture.configuration_sha256,"report":report,
            "attempts":[{k:getattr(a,k) for k in ("case_id","status","cost_microunits","elapsed_ms")} for a in capture.attempts],
        })
    result_ledger={"format":CAPTURES,"plan_sha256":ledger.plan_sha256,"captures":minimized}
    result=review(packet,verified_plan,result_ledger)
    result.update(raw_capture_sha256=digest(ledger.model_dump(exclude_unset=True)),
                  offline_scores_recomputed_from_returned_plans=True,
                  external_candidate_execution_observed=False,
                  raw_inputs_or_plans_included=False)
    return result_ledger,result


def _private_json(path,value):
    payload=(json.dumps(value,indent=2,allow_nan=False)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as handle:handle.write(payload)


def _output(directory,documents):
    """Exclusive private directory; never overwrite existing artifacts."""
    directory.mkdir(mode=0o700,parents=False,exist_ok=False)
    try:
        for name,value in documents.items():_private_json(directory/name,value)
    except BaseException:
        shutil.rmtree(directory)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="operation",required=True)
    for name in ("prepare","score"):
        p=sub.add_parser(name)
        p.add_argument("--process-export",type=Path,required=True)
        p.add_argument("--suite",type=Path,required=True)
        p.add_argument("--out-dir",type=Path,required=True)
        if name=="prepare":
            p.add_argument("--design",type=Path,required=True)
            p.add_argument("--confirm-export-inputs",action="store_true")
        else:
            p.add_argument("--plan",type=Path,required=True)
            p.add_argument("--raw-captures",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        packet=_load(args.process_export);suite=_load(args.suite)
        if args.operation=="prepare":
            plan,challenge=prepare(packet,suite,_load(args.design),confirm_export_inputs=args.confirm_export_inputs)
            _output(args.out_dir,{"plan.json":plan,"challenge.private.json":challenge})
        else:
            ledger,result=score(packet,suite,_load(args.plan),_load(args.raw_captures))
            _output(args.out_dir,{"captures.minimized.json":ledger,"review.json":result})
    except (OSError,ValueError,KeyError,TypeError,RecursionError,OverflowError):
        parser.exit(2,"Invalid replay input or output unavailable. No external agent or business tool was called.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
