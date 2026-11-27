"""Portable minimized connector snapshots, separately approved from job dispatch.

No raw source payload, provider token, resource identity, free-form attributes
or implicit cross-source case linkage. The digest covers every exported row.
"""
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

FORMAT="openworkgraph.connector-event-snapshot.v1"
MAX_EVENTS=5000
MAX_BYTES=5*1024*1024
DIGEST=r"^[a-f0-9]{64}$"
PROFILE=r"^rest_[a-f0-9]{24}$"


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)


def utc(value):
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        if dt.utcoffset() is None or not 1900<=dt.year<=2100:
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (AttributeError,ValueError,OverflowError):
        raise ValueError("invalid timezone-qualified timestamp") from None


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()


class Event(Strict):
    event_id: str=Field(pattern=DIGEST)
    case_id: str=Field(pattern=DIGEST)
    activity: str=Field(min_length=1,max_length=160)
    observed_at: str=Field(max_length=40)

    @model_validator(mode="after")
    def validate_values(self):
        if not self.activity.strip() or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF or ord(c) in (0xFFFE,0xFFFF) for c in self.activity):
            raise ValueError("invalid activity label")
        self.observed_at=utc(self.observed_at)
        return self


class Manifest(Strict):
    format: Literal["openworkgraph.connector-event-snapshot.v1"]=FORMAT
    profile_id: str=Field(pattern=PROFILE)
    profile_revision: StrictInt=Field(ge=1,le=1_000_000)
    profile_sha256: str=Field(pattern=DIGEST)
    provider: Literal["rest","google","sql","slack","salesforce","servicenow","sap","dataverse"]
    since: str=Field(max_length=40)
    until: str=Field(max_length=40)
    checkpoint: str=Field(max_length=40)
    event_count: StrictInt=Field(ge=1,le=MAX_EVENTS)
    case_count: StrictInt=Field(ge=1,le=MAX_EVENTS)
    activity_mode: Literal["coded","labels"]
    rows_sha256: str=Field(pattern=DIGEST)

    @model_validator(mode="after")
    def validate_window(self):
        self.since,self.until,self.checkpoint=map(utc,(self.since,self.until,self.checkpoint))
        if not self.since<self.until or self.case_count>self.event_count:
            raise ValueError("invalid snapshot range or counts")
        # Event time and the source change checkpoint are distinct; a provider
        # can report an older event late or a future-dated business event.
        return self


class Snapshot(Strict):
    manifest: Manifest
    events: list[Event]=Field(min_length=1,max_length=MAX_EVENTS)

    @model_validator(mode="after")
    def exact(self):
        m=self.manifest
        rows=[e.model_dump() for e in self.events]
        if len(rows)!=m.event_count or len({e.case_id for e in self.events})!=m.case_count:
            raise ValueError("snapshot count mismatch")
        if len({e.event_id for e in self.events})!=len(rows):
            raise ValueError("duplicate snapshot event identity")
        if rows!=sorted(rows,key=lambda r:(r["observed_at"],r["event_id"])):
            raise ValueError("snapshot rows must be canonically sorted")
        if any(not m.since<=e.observed_at<m.until for e in self.events):
            raise ValueError("event outside declared snapshot window")
        if m.activity_mode=="coded" and any(not e.activity.startswith("A_") or len(e.activity)!=66 or any(c not in "0123456789abcdef" for c in e.activity[2:]) for e in self.events):
            raise ValueError("coded activities must be pseudonymous")
        if digest(rows)!=m.rows_sha256:
            raise ValueError("snapshot digest mismatch")
        return self


def manifest_digest(manifest):
    return digest(manifest.model_dump() if isinstance(manifest,Manifest) else manifest)
