"""Isolated Gateway with synthetic exact published snapshots and crosswalks."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone, timedelta

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from gateway.db import GatewayDB
from gateway.connector_jobs import Jobs,Create
from gateway.connector_event_publications import Publications,Approve,Upload
from gateway.enterprise_app import create_enterprise_app
from gateway.settings import GatewaySettings
from gateway.hardening import HardeningSettings
from shared.connector_event_snapshot import Manifest,Snapshot,digest,manifest_digest
from shared.connector_crosswalk import Crosswalk

scratch=Path(os.environ["OWG_BROWSER_TEST_DIR"])
os.environ["OWG_GATEWAY_ADMIN_TOKEN_API"]="enabled"
url="sqlite:///"+str(scratch/"gateway.db")
db=GatewayDB(url);db.init()
jobs=Jobs(db);jobs.initialize()
pubs=Publications(jobs);pubs.initialize()
now=datetime.now(timezone.utc).replace(microsecond=0)
for i in (1,2):
    profile="rest_"+str(i)*24
    row={"event_id":str(i)*64,"case_id":str(i+2)*64,"activity":"A_"+str(i)*64,
         "observed_at":(now-timedelta(hours=2)).isoformat(timespec="microseconds")}
    m=Manifest(profile_id=profile,profile_revision=1,profile_sha256=str(i)*64,provider="rest",
       since=(now-timedelta(days=1)).isoformat(),until=now.isoformat(),
       checkpoint=now.isoformat(),event_count=1,case_count=1,activity_mode="coded",rows_sha256=digest([row]))
    snap=Snapshot(manifest=m,events=[row])
    made=jobs.create(Create(organization_id="customer",name="Synthetic source "+str(i),provider="rest",
       profile_id=profile,profile_revision=1,profile_sha256=str(i)*64,interval_minutes=60,
       confirm_dispatch=True,confirm_counts_only_receipts=True),"owner:fixture")
    j=made["job"]
    g=pubs.approve("customer",j["job_id"],Approve(expected_job_revision=1,manifest=m,
       expected_publication_revision=0,retention_hours=24,confirm_event_disclosure=True),"owner:fixture")
    pubs.upload(made["worker_token"],Upload(grant_id=g["grant_id"],snapshot=snap))
    rows=[{"case_id":row["case_id"],"match_tag":"e"*64}]
    a=Crosswalk(job_id=j["job_id"],grant_id=g["grant_id"],publication_revision=g["revision"],
       manifest_sha256=manifest_digest(m),batch_id="a"*32,domain="purchase_order",key_check="f"*64,
       basis="reviewed_business_record",rows=rows,rows_sha256=digest(rows))
    (scratch/("crosswalk"+str(i)+".json")).write_text(a.model_dump_json(),encoding="utf-8")
app=create_enterprise_app(settings=GatewaySettings(database_url=url,admin_token="test-admin",
    enrollment_token="test-enroll"),hardening=HardeningSettings())
