"""Pure no-database quality verification and compatibility with #1602 report v1."""
from copy import deepcopy
from random import Random
from types import SimpleNamespace
import pytest

from shared.connector_bundle_import import decode_value,MAPPING,FORMAT as BUNDLE_FORMAT
from shared.connector_event_snapshot import Manifest,digest,manifest_digest,utc
from shared.connector_quality_verification import Policy,SourceDeclaration,calculate,verify_quality,FORMAT
from shared.connector_publication_quality import summarize,evaluate

# Frozen reference contract from the Gateway report in PR #1602.
# Deliberately separate report assembly, using the shared v1 arithmetic.
def gateway_v1_reference(org,spec,loaded):
    sources=[]
    for pin,job,d,snap in loaded:
        policy=spec.source_overrides.get(pin.job_id,spec.policy).model_dump()
        m=snap.manifest
        metrics=summarize([e.model_dump() for e in snap.events],day_precision=m.provider=="sap")
        checks=evaluate(metrics,policy)
        status="fail" if any(g["status"]=="fail" for g in checks) else (
            "indeterminate" if any(g["status"]=="indeterminate" for g in checks) else "pass")
        # No labels, case/event identities, source credentials or raw rows.
        sources.append({**pin.model_dump(),"provider":m.provider,
                        "window":{"since":m.since,"until":m.until,"change_checkpoint":m.checkpoint},
                        "expires_at":d["expires_at"],"activity_mode":m.activity_mode,
                        "policy_origin":"source_override" if pin.job_id in spec.source_overrides else "default",
                        "policy":policy,"metrics":metrics,"gates":checks,"status":status})
    summary={"sources":len(sources),"events":sum(x["metrics"]["events"] for x in sources),
             "source_local_cases":sum(x["metrics"]["cases"] for x in sources),
             "passing_sources":sum(x["status"]=="pass" for x in sources),
             "failing_sources":sum(x["status"]=="fail" for x in sources),
             "indeterminate_sources":sum(x["status"]=="indeterminate" for x in sources)}
    report={"format":FORMAT,"organization_id":org,"summary":summary,"sources":sources,
            "default_policy":spec.policy.model_dump(),
            "expires_at":min(x["expires_at"] for x in sources),
            "all_selected_sources_pass":all(x["status"]=="pass" for x in sources),
            "provider_completeness_verified":False,"business_identity_verified":False,
            "physical_occurrence_times_verified":False,"cross_source_identity_inferred":False,
            "interpretation":"Thresholds apply to the selected disclosed sample only. Singleton cases and repeated labels may be legitimate. Reported timestamp ties are not verified simultaneous actions. SAP business dates do not establish occurrence order. Source-local cases are not distinct enterprise business cases."}
    return {**report,"quality_sha256":digest(report)}



def fixture(rng,n,count,day=False):
    manifests=[];events=[];pins=[]
    start=utc("2026-01-01T00:00:00Z");end=utc("2026-01-02T00:00:00Z")
    for i in range(n):
        job="cj_"+f"{i+1:032x}";grant="cep_"+f"{i+11:032x}"
        rows=[{"event_id":f"{j+1:064x}","case_id":f"{rng.randrange(1,max(2,count//3+1)):064x}",
            "activity":"A_"+f"{rng.randrange(1,8):064x}",
            "observed_at":start if day and i==0 else utc(f"2026-01-01T00:00:{rng.randrange(60):02}Z")}
            for j in range(count)]
        rows.sort(key=lambda r:(r["observed_at"],r["event_id"]))
        m=Manifest(profile_id="rest_"+f"{i+1:024x}",profile_revision=1,profile_sha256="1"*64,
            provider="sap" if day and i==0 else "rest",since=start,until=end,checkpoint=end,
            event_count=len(rows),case_count=len({r["case_id"] for r in rows}),
            activity_mode="coded",rows_sha256=digest(rows)).model_dump()
        manifests.append({"job_id":job,"grant_id":grant,"manifest_sha256":manifest_digest(m),"manifest":m})
        pins.append({"job_id":job,"grant_id":grant,"manifest_sha256":manifest_digest(m),
                     "publication_revision":1,"expires_at":1900000000.25})
        events.extend({"source_namespace":job,**r} for r in rows)
    return {"format":BUNDLE_FORMAT,"manifests":manifests,"events":events,"event_count":len(events),
            "cross_source_identity_verified":False,"provider_completeness_verified":False,"mapping":MAPPING},pins


def report(value,pins,policy=None,overrides=None):
    original=decode_value(value)
    default=Policy.model_validate(policy or {"min_events":1,"min_cases":1})
    overrides={j:Policy.model_validate(p) for j,p in (overrides or {}).items()}
    declarations=[SourceDeclaration.model_validate({**pin,
        "policy_origin":"source_override" if pin["job_id"] in overrides else "default",
        "policy":overrides.get(pin["job_id"],default)}) for pin in pins]
    actual=calculate("customer",default,declarations,original)
    loaded=[]
    for src,pin in zip(original.manifests,pins):
        pin_only={k:v for k,v in pin.items() if k!="expires_at"}
        pin_obj=SimpleNamespace(job_id=src.job_id,model_dump=lambda p=pin_only:p)
        # Exact source-local Event objects in the original Gateway contract.
        from shared.connector_event_snapshot import Event
        rows=[Event.model_validate(e.model_dump(exclude={"source_namespace"})) for e in original.events
              if e.source_namespace==src.job_id]
        loaded.append((pin_obj,None,{"expires_at":pin["expires_at"]},SimpleNamespace(manifest=src.manifest,events=rows)))
    assert actual==gateway_v1_reference("customer",SimpleNamespace(policy=default,source_overrides=overrides),loaded)
    return actual


def redigest(q):
    q["quality_sha256"]=digest({k:v for k,v in q.items() if k!="quality_sha256"})


def test_generated_report_parity_counts_permutations_and_redigested_tampering():
    rng=Random(813)
    for t in range(300):
        n=rng.randrange(1,5);count=rng.randrange(1,55)
        value,pins=fixture(rng,n,count,day=t%3==0)
        bundle=decode_value(value);q=report(value,pins)
        verified,policy=verify_quality(q,bundle,expected_org="customer",expected_pins=pins)
        assert verified==q and q["summary"]["events"]==n*count
        for s in q["sources"]:
            rows=[e for e in value["events"] if e["source_namespace"]==s["job_id"]]
            cases={e["case_id"] for e in rows}
            singleton=sum(sum(e["case_id"]==c for e in rows)==1 for c in cases)
            assert s["metrics"]["singleton_cases"]==singleton
            assert s["metrics"]["cases"]==len(cases)
            assert s["metrics"]["events"]==count
            assert s["metrics"]["singleton_case_rate_bps"]==(10000*singleton+len(cases)-1)//len(cases)
        value["events"].reverse();value["manifests"].reverse()
        assert verify_quality(q,decode_value(value),expected_org="customer",expected_pins=pins)==(verified,policy)
        for mutate in (lambda bad:bad["sources"][0]["metrics"].update(events=count+1),
                       lambda bad:bad.update(all_selected_sources_pass=1),
                       lambda bad:bad["sources"][0].update(publication_revision=2)):
            bad=deepcopy(q);mutate(bad);redigest(bad)
            with pytest.raises(ValueError,match="quality_report_integrity_failure"):
                verify_quality(bad,bundle,expected_org="customer",expected_pins=pins)


def test_date_precision_and_source_override_do_not_silently_pass():
    value,pins=fixture(Random(813),2,12,day=True)
    b=decode_value(value)
    q=report(value,pins,{"max_timestamp_tie_event_rate_bps":10000})
    assert q["summary"]["indeterminate_sources"]==1
    with pytest.raises(ValueError,match="quality_gates_not_satisfied"):
        verify_quality(q,b,expected_org="customer",expected_pins=pins)
    q=report(value,pins,{"min_events":13})
    with pytest.raises(ValueError,match="quality_gates_not_satisfied"):
        verify_quality(q,b,expected_org="customer",expected_pins=pins)
    q=report(value,pins,{"max_timestamp_tie_event_rate_bps":10000},
             overrides={pins[0]["job_id"]:{"min_events":1}})
    assert verify_quality(q,b,expected_org="customer",expected_pins=pins)[0]==q
