"""Explicit complete-scope portable export under the original organization fence."""
import json
from pydantic import StrictBool
from shared.connector_bundle_import import MAPPING,MAX_BYTES,FORMAT as BUNDLE_FORMAT
from shared.connector_portable_linkage import Envelope,FORMAT,reconstruct
from shared.connector_event_snapshot import digest
from shared.connector_case_projection import project_rows
from .connector_case_review import Export
from .connector_jobs import JobError


class PortableExport(Export):
    confirm_complete_source_disclosure:StrictBool


def export_portable(reviewed,org,spec,principal):
    if not (spec.confirm_link_assertions and spec.confirm_export and spec.confirm_complete_source_disclosure):
        raise JobError("explicit_complete_source_disclosure_required",403)
    with reviewed.jobs._tx(org) as c:
        loaded=reviewed._read(c,org,spec)
        assigned,report=reviewed._compute(org,spec,loaded)
        if report["review_sha256"]!=spec.expected_review_sha256:
            raise JobError("cross_source_review_changed")
        cases,manifests,_,count,scope,expires=loaded
        # The complete originals are required to independently recompute
        # the source manifest digests, including excluded unlinked cases.
        original=sorted(({"source_namespace":job,**e}
                         for (job,_),events in cases.items() for e in events),
                        key=lambda r:(r["source_namespace"],r["observed_at"],r["event_id"]))
        source={"format":BUNDLE_FORMAT,"manifests":[{k:m[k] for k in
            ("job_id","grant_id","manifest","manifest_sha256")} for m in manifests],
            "events":original,"event_count":count,"cross_source_identity_verified":False,
            "provider_completeness_verified":False,"mapping":MAPPING}
        rows=project_rows(spec,loaded,assigned)
        value={"format":FORMAT,"organization_id":org,"source_bundle":source,
            "pins":[{k:v for k,v in m.items() if k!="manifest"} for m in manifests],
            "groups":[g.model_dump() for g in spec.groups],
            "confirm_many_to_one":spec.confirm_many_to_one,"include_unlinked":spec.include_unlinked,
            "scope_sha256":scope,"review_sha256":report["review_sha256"],
            "projected_rows_sha256":digest(rows)}
        try:
            envelope=Envelope.model_validate(value)
            projection,check=reconstruct(envelope)
            if [r.model_dump() for r in projection.events]!=rows or check!=report:
                raise ValueError("portable projection differs")
        except ValueError:
            raise JobError("portable_linkage_integrity_failure") from None
        out=envelope.model_dump()
        # Bound actual worst-case ASCII JSON encoding, not only row count.
        if len(json.dumps(out,ensure_ascii=True,separators=(",",":")).encode())>MAX_BYTES:
            raise JobError("portable_bundle_exceeds_import_byte_budget",422)
        reviewed._fresh(expires)
        reviewed._audit(c,org,spec,principal,"portable_export",
            {"review_sha256":report["review_sha256"],"complete_source_events":count,
             "projected_events":len(rows),"excluded_events_disclosed":count-len(rows)})
        reviewed._fresh(expires)
        return out
