"""Explicit offline exchange of minimized workforce monitoring summaries.

Shared high-entropy correlation key produces comparable opaque HMAC tags.
It is never persisted or exported. Tags permit exact declared identifier
overlap detection, not identity verification or proof of independent cases.
No automatic transport, organization tenancy or remote execution.
"""
from __future__ import annotations

from collections import defaultdict, Counter
from fractions import Fraction
import hashlib
import hmac
import time
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictBool, model_validator

from shared.agent_procurement import Strict, Alias, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_overview as local

FORMAT="openworkgraph.workforce-exchange.v1"
MAX_SITES=20
MAX_ROWS=20
MAX_CASES=1500
Count=Annotated[StrictInt,Field(ge=0,le=MAX_CASES)]
Epoch=Annotated[StrictInt,Field(ge=0,le=10**15)]
Digits=Annotated[str,Field(pattern=r"^(0|[1-9][0-9]{0,99})$")]
Positive=Annotated[str,Field(pattern=r"^[1-9][0-9]{0,99}$")]
Currency=Annotated[str,Field(pattern=r"^[A-Z]{3}$")]


class Rational(Strict):
    numerator: Digits
    denominator: Positive

    def value(self):
        return Fraction(int(self.numerator),int(self.denominator))


class Cost(Strict):
    lower_microunits: Rational
    upper_microunits: Rational | None

    @model_validator(mode="after")
    def ordered(self):
        if self.upper_microunits is not None and self.lower_microunits.value()>self.upper_microunits.value():
            raise ValueError("invalid cost interval")
        return self


class Interval(Strict):
    lower: Count
    upper: Count

    @model_validator(mode="after")
    def ordered(self):
        if self.lower>self.upper:raise ValueError("invalid count interval")
        return self


class Arm(Strict):
    cases: Count
    recorded: Count
    terminal: Count
    counts: dict[str,Interval]
    attribution_counts: dict[str,Count]
    variable_cost: Cost

    @model_validator(mode="after")
    def valid(self):
        if not self.terminal<=self.recorded<=self.cases:
            raise ValueError("invalid arm coverage")
        if set(self.counts)!=set(local.METRICS) or any(x.upper>self.cases for x in self.counts.values()):
            raise ValueError("invalid metric bounds")
        if set(self.attribution_counts)!={"pass","fail","unknown"} or sum(self.attribution_counts.values())!=self.cases:
            raise ValueError("invalid attribution counts")
        return self


class Row(Strict):
    journal_tag: Hash
    plan_tag: Hash
    currency: Currency
    cutoff_ms: Epoch
    scheduled_cases: Count
    started_cases: Count
    recorded_cases: Count
    credential_attributed: Count
    cohort_identity_available: StrictBool
    case_tags: list[Hash]=Field(max_length=MAX_CASES)
    source_tags: list[Hash]=Field(max_length=MAX_CASES)
    arms: dict[str,Arm]
    local_eligible: StrictBool
    rollback_review: StrictBool
    attention: StrictBool

    @model_validator(mode="after")
    def valid(self):
        if not self.credential_attributed<=self.recorded_cases<=self.started_cases<=self.scheduled_cases:
            raise ValueError("invalid row coverage")
        if set(self.arms) not in (set(),set(local.ARMS)):
            raise ValueError("invalid arm names")
        if sum(a.cases for a in self.arms.values())!=self.started_cases or sum(a.recorded for a in self.arms.values())!=self.recorded_cases:
            raise ValueError("arm totals disagree")
        if len(set(self.case_tags))!=len(self.case_tags) or len(set(self.source_tags))!=len(self.source_tags):
            raise ValueError("duplicate correlation tags")
        if self.cohort_identity_available:
            if len(self.case_tags)!=self.scheduled_cases or len(self.source_tags)!=self.recorded_cases:
                raise ValueError("correlation coverage disagrees")
        elif self.case_tags or self.source_tags or self.local_eligible:
            raise ValueError("unavailable cohort cannot be included")
        return self


class Packet(Strict):
    format: Literal["openworkgraph.workforce-exchange.v1"]
    installation_alias: Alias
    correlation_namespace_sha256: Hash
    exported_at_ms: Epoch
    unreadable_journals: Annotated[StrictInt,Field(ge=0,le=MAX_ROWS)]
    rows: list[Row]=Field(max_length=MAX_ROWS)

    @model_validator(mode="after")
    def valid(self):
        if len(self.rows)+self.unreadable_journals>MAX_ROWS:
            raise ValueError("installation capacity exceeded")
        if len({r.journal_tag for r in self.rows})!=len(self.rows):
            raise ValueError("duplicate journal tag")
        return self


class ExportRequest(Strict):
    installation_alias: Alias
    correlation_key_hex: Hash
    confirmed: StrictBool


class PinnedPacket(Strict):
    expected_packet_sha256: Hash
    packet: Packet


class ReviewRequest(Strict):
    expected_correlation_namespace_sha256: Hash
    maximum_packet_age_ms: Annotated[StrictInt,Field(ge=1,le=366*24*3600000)]
    packets: list[PinnedPacket]=Field(min_length=1,max_length=MAX_SITES)
    confirmed: StrictBool


def _tag(key,domain,value):
    return hmac.new(key,("owg-workforce-v1:"+domain+":"+value).encode("ascii"),hashlib.sha256).hexdigest()


def export(document):
    req=ExportRequest.model_validate(document)
    if not req.confirmed:raise ValueError("explicit exchange export consent required")
    key=bytes.fromhex(req.correlation_key_hex)
    if len(set(key))<16:raise ValueError("use a randomly generated 32-byte correlation key")
    records,current,unreadable=local.read_inventory()
    now=int(time.time()*1000)
    overlaps=local._overlaps(records)
    rows=[]
    for scope,state,history in records:
        row=local._row(scope,state,history,current.get(scope[0]),now,overlaps[scope])
        tokens=local._tokens(state)
        arms={name:{k:a[k] for k in ("cases","recorded","terminal","counts","attribution_counts","variable_cost")}
              for name,a in row["arms"].items()}
        rows.append({"journal_tag":_tag(key,"journal",digest(list(scope))),
            "plan_tag":_tag(key,"plan",row["plan_sha256"]),"currency":row["currency"],
            "cutoff_ms":row["as_of_ms"],"scheduled_cases":row["scheduled_cases"],
            "started_cases":row["started_window_cases"],"recorded_cases":row["recorded_cases"],
            "credential_attributed":row["credential_authenticated_observations"],
            "cohort_identity_available":tokens is not None,
            "case_tags":sorted(_tag(key,"case",v) for v in tokens[0]) if tokens else [],
            "source_tags":sorted(_tag(key,"source",v) for v in tokens[1]) if tokens else [],
            "arms":arms,"local_eligible":row["included_in_totals"],
            "rollback_review":row["priority"]=="rollback_review",
            "attention":row["priority"]=="attention"})
    packet=Packet.model_validate({"format":FORMAT,"installation_alias":req.installation_alias,
        "correlation_namespace_sha256":_tag(key,"namespace","v1"),"exported_at_ms":now,
        "unreadable_journals":len(unreadable),"rows":sorted(rows,key=lambda x:x["journal_tag"])}).model_dump()
    return {"packet":packet,"packet_sha256":digest(packet),"source_identity_verified":False,
            "result_persisted":False,"execution_authorized":False}


def _collisions(rows):
    indexes={k:defaultdict(set) for k in ("plan","case","source")}
    for scope,row in rows:
        indexes["plan"][row.plan_tag].add(scope)
        for t in row.case_tags:indexes["case"][t].add(scope)
        for t in row.source_tags:indexes["source"][t].add(scope)
    result=defaultdict(set)
    for k,index in indexes.items():
        for scopes in index.values():
            if len(scopes)>1:
                for s in scopes:result[s].add(k+"_tag_overlap")
    return result


def review(document,*,now=None):
    req=ReviewRequest.model_validate(document)
    if not req.confirmed:raise ValueError("explicit exchange review consent required")
    now=int(time.time()*1000) if now is None else now
    aliases=set();rows=[];sites={};unreadable=0
    for p in req.packets:
        packet=p.packet;alias=packet.installation_alias
        if digest(packet.model_dump())!=p.expected_packet_sha256:
            raise ValueError("packet pin mismatch")
        if packet.correlation_namespace_sha256!=req.expected_correlation_namespace_sha256:
            raise ValueError("correlation namespace mismatch")
        if alias.casefold() in aliases:raise ValueError("duplicate installation alias")
        aliases.add(alias.casefold())
        if not 0<=now-packet.exported_at_ms<=req.maximum_packet_age_ms:
            raise ValueError("stale or future installation packet")
        unreadable+=packet.unreadable_journals
        sites[alias]={"installation_alias":alias,"packet_sha256":p.expected_packet_sha256,
                      "exported_at_ms":packet.exported_at_ms,"unreadable_journals":packet.unreadable_journals}
        for row in packet.rows:
            rows.append(((alias,row.journal_tag),row))
    overlaps=_collisions(rows)
    totals={};output=[]
    for scope,row in sorted(rows,key=lambda x:x[0]):
        alias,tag=scope;reasons=set(overlaps[scope])
        if not row.local_eligible:reasons.add("excluded_at_source")
        if not row.cohort_identity_available:reasons.add("unavailable_cohort_identity")
        if row.cutoff_ms>sites[alias]["exported_at_ms"]:reasons.add("future_snapshot_cutoff")
        output.append({"installation_alias":alias,"journal_tag":tag,"currency":row.currency,
            "cutoff_ms":row.cutoff_ms,"scheduled_cases":row.scheduled_cases,"started_cases":row.started_cases,
            "recorded_cases":row.recorded_cases,"credential_attributed":row.credential_attributed,
            "rollback_review":row.rollback_review,"attention":row.attention,
            "included_in_totals":not reasons,"exclusion_reasons":sorted(reasons)})
        if reasons:continue
        g=totals.setdefault(row.currency,{"currency":row.currency,"journals":0,"scheduled_cases":0,
            "started_cases":0,"recorded_cases":0,"credential_attributed":0,
            "arms":{arm:local._arm_zero() for arm in local.ARMS}})
        g["journals"]+=1
        for k in ("scheduled_cases","started_cases","recorded_cases","credential_attributed"):
            g[k]+=getattr(row,k)
        for arm,a in row.arms.items():local._add_arm(g["arms"][arm],a.model_dump())
    for g in totals.values():g["arms"]={k:local._finish_arm(v) for k,v in g["arms"].items()}
    output.sort(key=lambda r:(not r["rollback_review"],not r["attention"],r["installation_alias"],r["journal_tag"]))
    result={"format":"openworkgraph.workforce-exchange-review.v1","installations":[sites[k] for k in sorted(sites)],
        "as_of_ms":now,"journal_count":len(rows)+unreadable,"unreadable_journals":unreadable,
        "included_journals":sum(r["included_in_totals"] for r in output),
        "excluded_journals":sum(not r["included_in_totals"] for r in output),
        "totals_may_be_incomplete":bool(unreadable or any(not r["included_in_totals"] for r in output)),
        "currency_totals":[totals[k] for k in sorted(totals)],"journals":output,
        "source_identity_verified":False,"business_case_independence_verified":False,
        "realized_savings_verified":False,"result_persisted":False,"execution_authorized":False,
        "limitations":["Explicit offline installation snapshots, not a live organization inventory.",
            "Caller-supplied digest pins establish content equality, not source authentication. Obtain pins separately from trusted operators.",
            "Common-key HMAC tags detect exact declared identifier matches; different tokens may still refer to the same business case. Tags are linkable within this namespace.",
            "All colliding journals are excluded, including locally excluded rows. Unreadable journals cannot be checked for overlap.",
            "Currency and arm totals remain separate declared measurements, not causal financial benefits."]}
    return {**result,"report_sha256":digest(result)}
