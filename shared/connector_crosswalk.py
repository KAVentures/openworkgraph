"""Portable minimized crosswalks. Match tags are keyed, scoped and temporary.

These are operator-prepared assertions, not provider attestations. Never
include native source IDs, external business identifiers or the HMAC key.
"""
import re
from typing import Literal
from pydantic import Field,StrictInt,StrictBool,model_validator
from .connector_event_snapshot import Strict,DIGEST,digest

FORMAT="openworkgraph.connector-crosswalk.v1"
MAX_ROWS=2000
MAX_BYTES=1024*1024
JOB=r"^cj_[a-f0-9]{32}$"
GRANT=r"^cep_[a-f0-9]{32}$"


class Entry(Strict):
    case_id:str=Field(pattern=DIGEST)
    match_tag:str=Field(pattern=DIGEST)


class Crosswalk(Strict):
    format:Literal["openworkgraph.connector-crosswalk.v1"]=FORMAT
    job_id:str=Field(pattern=JOB)
    grant_id:str=Field(pattern=GRANT)
    publication_revision:StrictInt=Field(ge=1)
    manifest_sha256:str=Field(pattern=DIGEST)
    batch_id:str=Field(pattern=r"^[a-f0-9]{32}$")
    domain:str=Field(pattern=r"^[a-z][a-z0-9_.-]{0,39}$")
    key_check:str=Field(pattern=DIGEST)
    basis:Literal["reviewed_native_reference","reviewed_business_record","operator_assertion"]
    rows:list[Entry]=Field(min_length=1,max_length=MAX_ROWS)
    rows_sha256:str=Field(pattern=DIGEST)

    @model_validator(mode="after")
    def exact(self):
        rows=[r.model_dump() for r in self.rows]
        if len({r["case_id"] for r in rows})!=len(rows):
            raise ValueError("one business match tag per case is required")
        if rows!=sorted(rows,key=lambda r:r["case_id"]) or digest(rows)!=self.rows_sha256:
            raise ValueError("crosswalk row order or digest mismatch")
        return self


def artifact_digest(value):
    return digest(value.model_dump() if isinstance(value,Crosswalk) else value)
