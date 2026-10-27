"""Metadata-only lineage for an exact shared-opportunity SDK export."""
from hashlib import sha256
from io import BytesIO
import json
from typing import Annotated,Literal
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
from pydantic import Field,StrictBool,StrictInt,model_validator
from .process_hierarchy import Strict,Reference,Digest
from .analysis_snapshot import content_digest

MAX_PROJECTS=50
MAX_ARCHIVE_BYTES=4*1024*1024
MANIFEST_FILE='shared-opportunity.lineage.json'


def spec_digest(spec):
    # Same specification digest as the existing generated runtime/monitor.
    return sha256(json.dumps(spec,sort_keys=True,allow_nan=False).encode()).hexdigest()


class Basis(Strict):
    format:Literal['openworkgraph.shared-opportunity-project.v1']='openworkgraph.shared-opportunity-project.v1'
    organization_id:Annotated[str,Field(min_length=1,max_length=200)]
    opportunity:Reference
    approved_opportunity:Reference
    process:Reference
    assessment_sha256:Digest
    assessed_in_revision:Annotated[StrictInt,Field(ge=1,le=50)]
    spec_sha256:Digest
    base_archive_sha256:Digest
    execution_authorized:Literal[False]=False
    spending_authorized:Literal[False]=False
    deployment_verified:Literal[False]=False
    per_run_binary_attribution_verified:Literal[False]=False
    business_outcomes_verified:Literal[False]=False

    @model_validator(mode='after')
    def same_root(self):
        if len({self.opportunity.workflow_id,self.approved_opportunity.workflow_id,self.process.workflow_id})!=1:raise ValueError('Project lineage must bind one existing root')
        if self.assessed_in_revision>self.approved_opportunity.revision or self.approved_opportunity.revision>self.opportunity.revision:raise ValueError('Opportunity approval chronology is inconsistent')
        return self


class Manifest(Basis):
    project_id:Annotated[str,Field(pattern=r'^project_[a-f0-9]{64}$')]

    @model_validator(mode='after')
    def exact_identity(self):
        if self.project_id!='project_'+content_digest(self.model_dump(mode='json',exclude={'project_id'})):raise ValueError('Project identity differs from its exact manifest')
        return self


class Saved(Strict):
    format:Literal['openworkgraph.shared-opportunity-project-record.v1']='openworkgraph.shared-opportunity-project-record.v1'
    manifest:Manifest
    archive_sha256:Digest
    created_at:str
    created_by_actor_id:str
    archive_stored:Literal[False]=False
    source_receipts_stored:Literal[False]=False


class ExportRequest(Strict):
    expected_opportunity:Reference
    expected_project_revision:Annotated[StrictInt,Field(ge=0,le=MAX_PROJECTS)]
    expected_access_revision:Annotated[StrictInt,Field(ge=1)]
    user_confirmed:StrictBool=False


class RegenerateRequest(Strict):
    expected_record_sha256:Digest
    expected_access_revision:Annotated[StrictInt,Field(ge=1)]
    user_confirmed:StrictBool=False


class Remove(Strict):
    expected_project_revision:Annotated[StrictInt,Field(ge=1,le=2147483647)]
    expected_access_revision:Annotated[StrictInt,Field(ge=1)]
    user_confirmed:StrictBool=False


def link_archive(base,basis):
    """Add one deterministic manifest; preserve every original entry byte."""
    if len(base)>MAX_ARCHIVE_BYTES:raise ValueError('Generated archive exceeds its bound')
    parsed=Basis.model_validate(basis).model_dump(mode='json')
    if sha256(base).hexdigest()!=parsed['base_archive_sha256']:raise ValueError('Base archive digest differs')
    manifest=Manifest.model_validate({**parsed,'project_id':'project_'+content_digest(parsed)}).model_dump(mode='json')
    output=BytesIO()
    with ZipFile(BytesIO(base)) as source,ZipFile(output,'w',compression=ZIP_DEFLATED) as target:
        names=source.namelist()
        if len(names)!=len(set(names)) or MANIFEST_FILE in names:raise ValueError('Generated archive has conflicting entries')
        if spec_digest(json.loads(source.read('workflow.spec.json')))!=manifest['spec_sha256']:raise ValueError('Specification differs from its manifest')
        for item in source.infolist():target.writestr(item,source.read(item.filename))
        info=ZipInfo(MANIFEST_FILE,date_time=(1980,1,1,0,0,0));info.compress_type=ZIP_DEFLATED;info.external_attr=0o644<<16
        target.writestr(info,json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    content=output.getvalue()
    if len(content)>MAX_ARCHIVE_BYTES:raise ValueError('Linked archive exceeds its bound')
    return content,manifest
