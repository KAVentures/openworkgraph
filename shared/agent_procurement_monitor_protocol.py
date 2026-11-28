"""Portable monitoring producer wire contract, with no server imports."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .agent_procurement import Alias, Strict, digest
from .agent_procurement_router_pilot import Observation, Epoch
from .process_shadow_review import Hash

MAX_BATCH=100
TOKEN_RE=re.compile(r"^owgmon_[0-9a-f]{24}\.[A-Za-z0-9_-]{64}$")
GrantID=Annotated[str,Field(pattern=r"^[0-9a-f]{24}$")]
INGEST_PATH="/v1/process-catalog/procurement-producer/ingest"
BATCH_FORMAT="openworkgraph.procurement-monitor-producer-batch.v1"
ASSIGNMENT_FORMAT="openworkgraph.procurement-monitor-producer-assignment.v1"
RECEIPT_FORMAT="openworkgraph.procurement-monitor-producer-receipt.v1"

class Batch(Strict):
    format: Literal["openworkgraph.procurement-monitor-producer-batch.v1"]
    batch_id: Alias
    observations: list[Observation]=Field(min_length=1,max_length=MAX_BATCH)

    @model_validator(mode="after")
    def unique(self):
        for k in ("slot_id","case_token_sha256","source_evidence_sha256"):
            if len({getattr(o,k) for o in self.observations})!=len(self.observations):
                raise ValueError("duplicate observation")
        return self


class Slot(Strict):
    slot_id: Hash
    case_token_sha256: Hash
    expected_router_configuration_sha256: Hash
    already_recorded: StrictBool


class Assignment(Strict):
    format: Literal["openworkgraph.procurement-monitor-producer-assignment.v1"]
    grant_id: GrantID
    producer_alias: Alias
    window_id: Alias
    arm: Literal["incumbent","challenger"]
    expires_ms: Epoch
    window_start_ms: Epoch
    window_end_exclusive_ms: Epoch
    ingest_path: Literal["/v1/process-catalog/procurement-producer/ingest"]
    batch_format: Literal["openworkgraph.procurement-monitor-producer-batch.v1"]
    maximum_batch_observations: Annotated[StrictInt,Field(ge=1,le=MAX_BATCH)]
    slots: list[Slot]=Field(min_length=1,max_length=500)
    execution_authorized: Literal[False]
    credential_included: Literal[False]
    assignment_sha256: Hash

    @model_validator(mode="after")
    def valid(self):
        if self.window_start_ms>=self.window_end_exclusive_ms:
            raise ValueError("invalid assignment window")
        for field in ("slot_id","case_token_sha256"):
            if len({getattr(x,field) for x in self.slots})!=len(self.slots):
                raise ValueError("duplicate assignment slot or case")
        if digest(self.model_dump(exclude={"assignment_sha256"}))!=self.assignment_sha256:
            raise ValueError("assignment digest mismatch")
        return self


class Receipt(Strict):
    format: Literal["openworkgraph.procurement-monitor-producer-receipt.v1"]
    grant_id: GrantID
    batch_id: Alias
    accepted_observations: Annotated[StrictInt,Field(ge=1,le=MAX_BATCH)]
    idempotent_replay: StrictBool
    source_credential_authenticated: Literal[True]
    business_outcomes_independently_verified: Literal[False]
    execution_authorized: Literal[False]
