"""Review and prepare multiple source-host crosswalks as one pinned batch.

No provider requests, Gateway writes, raw source exports or automatic links.
Only exact existing local snapshots and operator-supplied reviewed mappings.
"""
import argparse
import json
import os
from pathlib import Path
import stat
from zipfile import ZipFile, ZIP_DEFLATED

from pydantic import Field, StrictBool, StrictInt, model_validator

from shared.connector_event_snapshot import Strict, Snapshot, digest, manifest_digest, MAX_BYTES as SNAPSHOT_BYTES
from shared.connector_crosswalk import Crosswalk, artifact_digest
from server import enterprise_connector_crosswalk as local
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws

FORMAT = "openworkgraph.source-host-crosswalk-batch.v1"
MAX_INPUT = 256 * 1024
MAX_SOURCES = 10
MAX_REQUEST = 3 * 1024 * 1024
MAX_ROWS = 10000


class Source(Strict):
    snapshot_file: str = Field(min_length=1,max_length=1024)
    mapping_file: str = Field(min_length=1,max_length=1024)
    job_id: str = Field(pattern=r"^cj_[a-f0-9]{32}$")
    grant_id: str = Field(pattern=r"^cep_[a-f0-9]{32}$")
    publication_revision: StrictInt = Field(ge=1)


class Batch(Strict):
    batch_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    domain: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,39}$")
    sources: list[Source] = Field(min_length=2,max_length=MAX_SOURCES)
    require_all_snapshot_cases_mapped: StrictBool
    confirm_batch_review: StrictBool
    confirm_match_tag_disclosure: StrictBool

    @model_validator(mode="after")
    def unique(self):
        for field in ("job_id","grant_id","snapshot_file","mapping_file"):
            if len({getattr(s,field) for s in self.sources})!=len(self.sources):
                raise ValueError("distinct source job, grant and input files required")
        return self


def read_private(path,limit):
    # All inputs are operator-created private regular files. Check the
    # descriptor, not a second path lookup, and bound bytes while reading.
    flags=os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)
    if Path(path).is_symlink():
        raise ValueError("private input must not be a symlink")
    fd=os.open(path,flags)
    with os.fdopen(fd,"rb") as stream:
        before=os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_mode&0o077
                or before.st_size>limit or hasattr(os,"getuid") and before.st_uid!=os.getuid()):
            raise ValueError("private bounded owner-only input required")
        data=stream.read(limit+1)
        after=os.fstat(stream.fileno())
        if (len(data)>limit or len(data)!=before.st_size
                or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=
                   (after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ValueError("private input changed during read")
        return data


def prepare_batch(spec,secret):
    if not spec.confirm_batch_review or not spec.confirm_match_tag_disclosure:
        raise ValueError("explicit batch and tag disclosure review required")
    if type(secret) is not bytes or len(secret)!=32:
        raise ValueError("dedicated 32-byte random crosswalk key required")
    reports=[];artifacts=[];profiles=set()
    # One local workspace lock prevents a local retained append from
    # changing one snapshot while another is being validated. This is
    # not a distributed lock across independent hosts or provider systems.
    with ws._LOCK:
        for entry in sorted(spec.sources,key=lambda s:s.job_id):
            snap=Snapshot.model_validate_json(read_private(entry.snapshot_file,SNAPSHOT_BYTES))
            mapping=local.Input.model_validate(rest._strict_json(read_private(entry.mapping_file,local.MAX_INPUT)))
            m=snap.manifest
            if m.profile_id in profiles:
                raise ValueError("one exact snapshot per local source profile required")
            profiles.add(m.profile_id)
            art=local.prepare(snap,mapping,job_id=entry.job_id,grant_id=entry.grant_id,
                              publication_revision=entry.publication_revision,batch_id=spec.batch_id,
                              domain=spec.domain,secret=secret)
            count=len(art.rows)
            if spec.require_all_snapshot_cases_mapped and count!=m.case_count:
                raise ValueError("complete snapshot case mapping required")
            reports.append({"job_id":entry.job_id,"grant_id":entry.grant_id,
                            "publication_revision":entry.publication_revision,
                            "provider":m.provider,"manifest_sha256":manifest_digest(m),
                            "artifact_sha256":artifact_digest(art),"mapped_cases":count,
                            "snapshot_cases":m.case_count,"unmapped_cases":m.case_count-count,
                            "basis":art.basis})
            artifacts.append(art)
        if sum(len(a.rows) for a in artifacts)>MAX_ROWS:
            raise ValueError("combined crosswalk row budget exceeded")
        request={"snapshots":[{"job_id":a.job_id,"grant_id":a.grant_id,
                               "publication_revision":a.publication_revision,
                               "manifest_sha256":a.manifest_sha256} for a in artifacts],
                 "crosswalks":[a.model_dump() for a in artifacts],
                 "confirm_inspection":True,"confirm_tag_processing":True,
                 "include_ambiguous":False,"include_unlinked":True}
        size=len(json.dumps(request,separators=(",",":"),ensure_ascii=True).encode())
        if size>MAX_REQUEST:
            raise ValueError("combined Gateway request exceeds byte budget")
        report={"format":FORMAT,"batch_id":spec.batch_id,"domain":spec.domain,
                "sources":reports,"source_count":len(reports),
                "mapped_cases":sum(r["mapped_cases"] for r in reports),
                "unmapped_cases":sum(r["unmapped_cases"] for r in reports),
                "snapshot_cases":sum(r["snapshot_cases"] for r in reports),
                "require_all_snapshot_cases_mapped":spec.require_all_snapshot_cases_mapped,
                "gateway_request_bytes":size,"raw_identifiers_exported":False,
                "gateway_grants_independently_verified":False,
                "business_identity_independently_verified":False,
                "automatic_linkage_authorized":False}
        report["batch_sha256"]=digest(report)
        return report,artifacts


def write_bundle(path,spec,secret,expected_sha256,*,confirm_export):
    if confirm_export is not True:
        raise ValueError("explicit batch export confirmation required")
    # Keep review and file creation under the same local lock. All
    # artifacts are rebuilt; no stale preview or partially prepared output.
    with ws._LOCK:
        report,artifacts=prepare_batch(spec,secret)
        if expected_sha256!=report["batch_sha256"]:
            raise ValueError("reviewed batch changed; prepare a new preview")
        path=Path(path)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,"wb") as stream:
                with ZipFile(stream,"w",compression=ZIP_DEFLATED) as archive:
                    for i,art in enumerate(artifacts,1):
                        archive.writestr(f"crosswalk-{i:02d}.json",art.model_dump_json())
                    archive.writestr("review-manifest.json",json.dumps(report,indent=2,sort_keys=True))
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return report


def main():
    parser=argparse.ArgumentParser(description="Review exact local crosswalks and export a pinned private batch.")
    sub=parser.add_subparsers(dest="command",required=True)
    for name in ("preview","export"):
        p=sub.add_parser(name);p.add_argument("--input",required=True)
        if name=="export":
            p.add_argument("--out",required=True)
            p.add_argument("--expected-sha256",required=True)
            p.add_argument("--confirm-export",action="store_true")
    args=parser.parse_args()
    try:
        spec=Batch.model_validate(rest._strict_json(read_private(args.input,MAX_INPUT)))
        secret=local.secret_from_env()
        if args.command=="preview":
            report,_=prepare_batch(spec,secret)
        else:
            report=write_bundle(args.out,spec,secret,args.expected_sha256,confirm_export=args.confirm_export)
        print(json.dumps(report,sort_keys=True))
    except Exception:
        # Never echo validation failures, private file paths or native IDs.
        raise SystemExit("crosswalk batch refused; review private files, exact source pins, consent, budgets and preview digest") from None


if __name__=="__main__":
    main()
