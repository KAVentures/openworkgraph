"""Paired decision behavior regression on explicitly reviewed, bounded test cases."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator, field_validator
from .process_decision_tables import evaluate_decisions, validate_date
from .process_model import ProcessModel
from .process_rule_conditions import validate_facts

FORMAT = "openworkgraph.paired-policy-regression.v1"
SELECTED = {"unique_match", "first_match"}
STATUSES = Literal["unique_match", "first_match", "gap", "overlap", "unknown",
    "unconfirmed", "not_yet_effective", "expired", "dependency_unresolved",
    "dependency_fact_conflict", "table_missing"]
MAX_EXPECTATIONS = 600


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Expected(Strict):
    status: STATUSES
    target_activity_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def selection_shape(self):
        if (self.status in SELECTED) != (self.target_activity_id is not None):
            raise ValueError("selected outcomes require a target; unresolved outcomes must not have one")
        return self


class Check(Strict):
    table_id: str = Field(min_length=1, max_length=64)
    before: Expected
    after: Expected


class Case(Strict):
    case_id: str = Field(pattern=r"^case_[1-9][0-9]{0,3}$")
    as_of_date: str
    facts: dict[str, Any]
    expectations: list[Check] = Field(min_length=1, max_length=12)

    @field_validator("as_of_date")
    @classmethod
    def explicit_date(cls, value):
        return validate_date(value)

    @model_validator(mode="after")
    def bounds(self):
        validate_facts(self.facts)
        if len({c.table_id for c in self.expectations}) != len(self.expectations):
            raise ValueError("one expectation per table per case")
        return self


class Suite(Strict):
    reviewed_by_user: StrictBool
    cases: list[Case] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def bounds(self):
        if not self.reviewed_by_user:
            raise ValueError("explicit case expectation review required")
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("distinct opaque test case labels required")
        if sum(len(c.expectations) for c in self.cases)>MAX_EXPECTATIONS:
            raise ValueError("too many reviewed expectations")
        return self


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _reference(packet):
    return {k:packet[k] for k in ("workflow_id","revision","content_sha256")}


def _actual(result):
    if result is None:
        return {"status":"table_missing","target_activity_id":None}
    return {"status":result["status"],"target_activity_id":result["selected_target_activity_id"]}


def _validate_expected(expect, table):
    if table is None:
        if expect.status!="table_missing":
            raise ValueError("a missing table must have an explicitly missing expectation")
        return
    if expect.status=="table_missing":
        raise ValueError("a retained table cannot have a missing-table expectation")
    if expect.target_activity_id is not None and expect.target_activity_id not in {
            row.target_activity_id for row in table.rows}:
        raise ValueError("expected selection is not a declared target in the corresponding revision")


def _classification(old_ok,new_ok,intent_changed):
    if intent_changed:
        if old_ok and new_ok:
            return "intended_change_matches"
        if not old_ok and new_ok:
            return "changed_expectation_after_matches"
        if old_ok:
            return "changed_expectation_after_mismatch"
        return "changed_expectations_both_mismatch"
    if old_ok and new_ok:
        return "stable_pass"
    if old_ok:
        return "regression"
    if new_ok:
        return "improvement"
    return "persistent_mismatch"


def compare(before,after,payload):
    if before["workflow_id"]!=after["workflow_id"] or before["revision"]>=after["revision"]:
        raise ValueError("select two ordered revisions of the same workflow")
    suite=Suite.model_validate(payload)
    old,new=(ProcessModel.model_validate(packet["process_model"]) for packet in (before,after))
    old_tables={t.id:t for t in old.decision_tables}
    new_tables={t.id:t for t in new.decision_tables}
    union=old_tables.keys()|new_tables.keys()
    # Validate all expected targets, stable IDs and case facts before evaluating
    # any case. Missing tables must be explicitly reviewed, not silently skipped.
    for case in suite.cases:
        for check in case.expectations:
            if check.table_id not in union:
                raise ValueError("unknown decision table in both revisions")
            _validate_expected(check.before,old_tables.get(check.table_id))
            _validate_expected(check.after,new_tables.get(check.table_id))
    counts=Counter()
    per_table={}
    case_rows=[]
    unreviewed=Counter()
    for case in suite.cases:
        a={r["table_id"]:r for r in evaluate_decisions(old,case.facts,case.as_of_date)}
        b={r["table_id"]:r for r in evaluate_decisions(new,case.facts,case.as_of_date)}
        checked={r.table_id:r for r in case.expectations}
        rows=[]
        for ident in sorted(union):
            left,right=_actual(a.get(ident)),_actual(b.get(ident))
            table_before,table_after=old_tables.get(ident),new_tables.get(ident)
            identity_discontinuous=(table_before is None or table_after is None or
                table_before.decision_activity_id!=table_after.decision_activity_id)
            changed=left!=right or identity_discontinuous
            if ident not in checked:
                unreviewed["table_case_pairs"]+=1
                unreviewed["changed_table_case_pairs"]+=int(changed)
                continue
            expectation=checked[ident]
            old_expected=expectation.before.model_dump()
            new_expected=expectation.after.model_dump()
            old_ok,new_ok=left==old_expected,right==new_expected
            different=old_expected!=new_expected
            kind=_classification(old_ok,new_ok,different)
            counts[kind]+=1
            tally=per_table.setdefault(ident,Counter())
            tally[kind]+=1
            tally["reviewed"]+=1
            tally["changed_observed_behavior"]+=int(changed)
            tally["changed_expectation"]+=int(different)
            tally["before_matches"]+=int(old_ok)
            tally["after_matches"]+=int(new_ok)
            rows.append({"table_id":ident,"before":left,"after":right,
                "before_expected":old_expected,"after_expected":new_expected,
                "before_matches":old_ok,"after_matches":new_ok,
                "classification":kind,"observed_behavior_changed":changed,
                "table_identity_discontinuous":identity_discontinuous})
        case_rows.append({"case_id":case.case_id,"as_of_date":case.as_of_date,"checks":rows,
            "all_after_expectations_match":all(r["after_matches"] for r in rows)})
    reviewed=sum(counts.values())
    return {"format":FORMAT,"before":_reference(before),"after":_reference(after),
        "suite_sha256":_digest(suite.model_dump()),"case_count":len(case_rows),
        "reviewed_expectations":reviewed,"classification_counts":dict(sorted(counts.items())),
        "before_matches":sum(v["before_matches"] for v in per_table.values()),
        "after_matches":sum(v["after_matches"] for v in per_table.values()),
        "cases_all_after_match":sum(r["all_after_expectations_match"] for r in case_rows),
        "per_table":[{"table_id":k,**dict(v)} for k,v in sorted(per_table.items())],
        "unreviewed_table_case_pairs":unreviewed["table_case_pairs"],
        "unreviewed_changed_table_case_pairs":unreviewed["changed_table_case_pairs"],
        "cases":case_rows,"complete_for_reviewed_expectations":True,
        "facts_returned":False,"facts_retained":False,
        "independent_expectations_verified":False,"general_policy_equivalence_proven":False,
        "policy_compliance_verified":False,"business_outcomes_verified":False,"execution_authorized":False,
        "interpretation":"Same explicitly dated facts for both saved revisions. Regression and improvement apply only to unchanged reviewed expectations. Changed expectations are reported separately. Unreviewed tables are not passed; stable table/target IDs are not proof of semantic identity or complete policy coverage."}
