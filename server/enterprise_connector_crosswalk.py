"""Prepare private crosswalks on the canonical local source host.

The input is an operator-reviewed two-column JSON mapping. Its native
identifiers are resolved through the exact adapter's retained case HMAC,
then through the publication HMAC. Missing cases are reported only as
counts. No raw identifiers, shared secret or private input leave the host.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
from typing import Literal

from pydantic import Field,StrictBool,model_validator
from shared.connector_event_snapshot import Strict,digest,manifest_digest
from shared.connector_crosswalk import Crosswalk,artifact_digest,MAX_ROWS,MAX_BYTES
from gateway.connector_jobs import profile_digest
from server import enterprise_rest_feed as rest
from server import enterprise_connector_snapshot as snapshots
from server import event_log_workspace as ws

from shared.enterprise_connector_contract import CROSSWALK_PROVIDERS as SUPPORTED
MAX_INPUT=2*1024*1024


class InputRow(Strict):
    native_case_id:str=Field(min_length=1,max_length=200)
    native_type:str|None=Field(default=None,max_length=80)
    business_key:str=Field(min_length=1,max_length=200)


class Input(Strict):
    rows:list[InputRow]=Field(min_length=1,max_length=MAX_ROWS)
    basis:Literal["reviewed_native_reference","reviewed_business_record","operator_assertion"]
    confirm_reviewed_mapping:StrictBool
    confirm_match_tag_disclosure:StrictBool

    @model_validator(mode="after")
    def valid(self):
        for row in self.rows:
            for v in (row.native_case_id,row.business_key):
                if v!=v.strip() or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in v):
                    raise ValueError("invalid private crosswalk identifier")
        return self


def secret_from_env():
    value=os.environ.get("OWG_CROSSWALK_SHARED_KEY","")
    try:
        if len(value)!=64 or value.lower()!=value:
            raise ValueError()
        raw=bytes.fromhex(value)
        if len(raw)!=32:
            raise ValueError()
        return raw
    except ValueError:
        raise ValueError("dedicated 32-byte random crosswalk key required") from None


def keyed(secret,domain,batch,purpose,value):
    # JSON array encoding separates fields even with arbitrary Unicode keys.
    data=json.dumps(["owg-crosswalk-v1",domain,batch,purpose,value],ensure_ascii=True,separators=(",",":")).encode()
    return hmac.new(secret,data,hashlib.sha256).hexdigest()


def native_case(p,row):
    provider=p.get("provider","rest")
    kind=row.native_type;value=row.native_case_id
    if provider in ("rest","sql"):
        if kind is not None:
            raise ValueError("native_type is not used for REST or SQL")
        value=rest._text(value,200)
        return rest._key(p,"case",value)
    if provider in ("databricks","snowflake","netsuite","workday"):
        # These adapters retain the exact approved view/report case string.
        # No numeric coercion, trimming, case folding or type inference.
        if kind is not None:
            raise ValueError("native_type is not used for curated event sources")
        return rest._key(p,provider+"_case",value)
    if provider=="oracle_fusion":
        # The selected business unit is part of the adapter case identity.
        # Never treat a requisition and purchase order as the same case.
        selection=next((x for x in p["config"]["selections"] if x["object_type"]==kind),None)
        if not selection or kind not in ("requisition","purchase_order") or value not in selection["header_ids"]:
            raise ValueError("native case is outside reviewed source scope")
        return rest._key(p,"oracle_fusion_case",kind+":"+str(selection["business_unit_id"])+":"+value)
    if provider=="salesforce":
        from server import salesforce_history_feed as sf
        selection=next((x for x in p["config"]["selections"] if x["object"]==kind),None)
        if not selection:
            raise ValueError("native case type is outside reviewed source scope")
        value=sf.sfid(value,sf.OBJECTS[kind][1])
        if selection["record_ids"] and value not in selection["record_ids"]:
            raise ValueError("native case is outside reviewed source scope")
        return rest._key(p,"salesforce_case",kind+":"+value)
    if provider=="servicenow":
        from server import servicenow_operations_feed as sn
        selection=next((x for x in p["config"]["selections"] if x["table"]==kind),None)
        if not selection or not sn.ID.fullmatch(value) or selection["record_ids"] and value not in selection["record_ids"]:
            raise ValueError("native case is outside reviewed source scope")
        return rest._key(p,"servicenow_case",kind+":"+value)
    if provider=="hubspot":
        from server import hubspot_history_feed as hs
        selection=next((x for x in p["config"]["selections"] if x["object_type"]==kind),None)
        value=hs.record_id(value)
        if not selection or value not in selection["record_ids"]:
            raise ValueError("native case is outside reviewed source scope")
        return rest._key(p,"hubspot_case",kind+":"+value)
    if provider=="dataverse":
        from server import dataverse_audit_feed as dv
        selection=next((x for x in p["config"]["selections"] if x["table"]==kind),None)
        value=dv.guid(value)
        if not selection or selection["record_ids"] and value not in selection["record_ids"]:
            raise ValueError("native case is outside reviewed source scope")
        return rest._key(p,"dataverse_case",kind+":"+value)
    if provider=="sap":
        from server import sap_procurement_feed as sap
        if kind not in (None,"purchase_order"):
            raise ValueError("SAP crosswalk requires purchase-order case")
        value=sap.po(value)
        if value not in p["config"]["purchase_orders"]:
            raise ValueError("native case is outside reviewed source scope")
        return rest._key(p,"sap_po_case",value)
    raise ValueError("adapter does not expose a reviewed native case resolver")


def prepare(snapshot,mapping,*,job_id,grant_id,publication_revision,batch_id,domain,secret):
    if not mapping.confirm_reviewed_mapping or not mapping.confirm_match_tag_disclosure:
        raise ValueError("explicit mapping review and match-tag disclosure required")
    if type(secret) is not bytes or len(secret)!=32:
        raise ValueError("dedicated 32-byte random crosswalk key required")
    m=snapshot.manifest
    with ws._LOCK:
        current=snapshots.prepare(m.profile_id,m.since,m.until,activity_mode=m.activity_mode,confirm_labels=True)
        if current.model_dump()!=snapshot.model_dump():
            raise ValueError("retained snapshot changed; reprepare from canonical source")
        c=rest._connect()
        try:
            p=rest._get(c,m.profile_id)
            if profile_digest(rest._public(p,rest.time.time()))!=m.profile_sha256:
                raise ValueError("retained profile pin changed")
            if m.provider not in SUPPORTED:
                raise ValueError("adapter does not expose a reviewed native case resolver")
            published={e.case_id for e in snapshot.events}
            rows=[];seen=set();missing=0
            for row in mapping.rows:
                case=rest._key(p,"publication_case","src:"+m.profile_id+":"+native_case(p,row))
                if case in seen:
                    raise ValueError("duplicate canonical native case in private input")
                seen.add(case)
                if case not in published:
                    missing+=1
                    continue
                tag=keyed(secret,domain,batch_id,"match",row.business_key)
                rows.append({"case_id":case,"match_tag":tag})
            # Never silently omit a selected source mapping: the operator
            # must fix missing cases or deliberately narrow the input.
            if missing:
                raise ValueError("private mapping contains "+str(missing)+" cases outside exact snapshot")
            rows.sort(key=lambda r:r["case_id"])
            return Crosswalk(job_id=job_id,grant_id=grant_id,publication_revision=publication_revision,
                manifest_sha256=manifest_digest(m),batch_id=batch_id,domain=domain,
                key_check=keyed(secret,domain,batch_id,"key_check",""),basis=mapping.basis,
                rows=rows,rows_sha256=digest(rows))
        finally:
            c.close()


def read_input(path):
    # Avoid following symlinks; inspect the opened descriptor rather than
    # a path that could have changed since validation.
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    with os.fdopen(fd,"rb") as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_size>MAX_INPUT:
            raise ValueError("input must be a private bounded regular file")
        data=f.read(MAX_INPUT+1)
        if len(data)>MAX_INPUT:
            raise ValueError("private input too large")
    try:
        return Input.model_validate(rest._strict_json(data))
    except Exception:
        raise ValueError("invalid private crosswalk input") from None


def write_output(path,value):
    data=value.model_dump_json().encode()
    if len(data)>MAX_BYTES:
        raise ValueError("crosswalk artifact exceeds byte limit")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data);f.flush();os.fsync(f.fileno())
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description="Prepare an exact private source-host crosswalk without sending raw identifiers.")
    for name in ("snapshot","input","out","job","grant","batch","domain"):
        parser.add_argument("--"+name,required=True)
    parser.add_argument("--publication-revision",required=True,type=int)
    args=parser.parse_args()
    try:
        result=prepare(snapshots.read_private(args.snapshot),read_input(args.input),job_id=args.job,
            grant_id=args.grant,publication_revision=args.publication_revision,batch_id=args.batch,
            domain=args.domain,secret=secret_from_env())
        write_output(args.out,result)
        print(json.dumps({"prepared":True,"rows":len(result.rows),"artifact_sha256":artifact_digest(result),
                          "raw_identifiers_exported":False,"provider_identity_independently_verified":False}))
    except Exception:
        # Pydantic error representations may contain native identifiers.
        raise SystemExit("crosswalk preparation failed; review private input, exact snapshot pins, key and source scope") from None


if __name__=="__main__":
    main()
