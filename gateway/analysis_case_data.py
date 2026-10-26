"""Explicit, revision-pinned case attachments; parent analysis access never grants case access."""
from collections import Counter
import json
from statistics import median
from typing import Annotated

from pydantic import Field, StrictBool, StrictInt, model_validator
from shared.analysis_case_dataset import CaseDataset, Digest, content_digest
from shared.analysis_snapshot import Strict
from .analysis_publications import PublicationError, Member

SCHEMA = '''
CREATE TABLE IF NOT EXISTS gateway_analysis_case_data (
 organization_id TEXT NOT NULL, view_id TEXT NOT NULL, revision INTEGER NOT NULL,
 payload TEXT NOT NULL, content_sha256 TEXT NOT NULL, access_revision INTEGER NOT NULL,
 published_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(organization_id,view_id,revision));
CREATE TABLE IF NOT EXISTS gateway_analysis_case_grants (
 organization_id TEXT NOT NULL, view_id TEXT NOT NULL, revision INTEGER NOT NULL,
 actor_id TEXT NOT NULL, parent_access_revision INTEGER NOT NULL,
 PRIMARY KEY(organization_id,view_id,revision,actor_id));
'''
READ = 'org:analysis:cases:read'
WRITE = 'org:analysis:cases:write'
MAX_ATTACHMENTS = 20


def initialize(store, conn):
    for sql in SCHEMA.split(';'):
        if sql.strip(): store.q(conn, sql)


def erase(store, conn, org, view_id):
    for table in ('gateway_analysis_case_grants','gateway_analysis_case_data'):
        store.q(conn, f'DELETE FROM {table} WHERE organization_id=? AND view_id=?',(org,view_id))


class Attach(Strict):
    revision: Annotated[StrictInt, Field(ge=1,le=10)]
    parent_content_sha256: Digest
    expected_parent_access_revision: Annotated[StrictInt, Field(ge=1)]
    dataset: CaseDataset
    user_confirmed: StrictBool = False
    case_sharing_confirmed: StrictBool = False


class CaseAccess(Strict):
    revision: Annotated[StrictInt, Field(ge=1,le=10)]
    content_sha256: Digest
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    expected_parent_access_revision: Annotated[StrictInt, Field(ge=1)]
    members: list[Member] = Field(default_factory=list,max_length=50)
    user_confirmed: StrictBool = False

    @model_validator(mode='after')
    def viewers_only(self):
        if any(m.role!='viewer' for m in self.members) or len({m.actor_id for m in self.members})!=len(self.members):
            raise ValueError('case grants need unique viewers')
        return self


class CaseDelete(Strict):
    revision: Annotated[StrictInt, Field(ge=1,le=10)]
    content_sha256: Digest
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    expected_parent_access_revision: Annotated[StrictInt, Field(ge=1)]
    user_confirmed: StrictBool = False


class CaseData:
    def __init__(self, parent): self.parent=parent

    def owner(self, c, p, view_id):
        s=self.parent
        s.scope(p,READ);s.scope(p,WRITE);s.scope(p,'org:analysis:write')
        meta=s.meta(c,p,view_id)
        # Even aggregate administrators/editors do not own the case disclosure.
        if meta['owner_actor_id']!=p.actor_id: raise PublicationError('Only the publication owner can manage case data',403)
        return meta

    def preflight(self,p,view_id):
        with self.parent.transaction(p) as c: self.owner(c,p,view_id)

    def load(self,c,p,view_id,revision,*,owner=False,verify=True):
        s=self.parent;s.scope(p,READ)
        meta=self.owner(c,p,view_id) if owner else s.meta(c,p,view_id)
        row=s.q(c,'SELECT payload, content_sha256, access_revision, published_at FROM gateway_analysis_case_data '
            'WHERE organization_id=? AND view_id=? AND revision=? AND deleted=0',(p.organization_id,view_id,revision)).fetchone()
        if not row:raise PublicationError('Case dataset not found',404)
        if meta['owner_actor_id']!=p.actor_id:
            grant=s.q(c,'SELECT parent_access_revision FROM gateway_analysis_case_grants WHERE organization_id=? AND view_id=? AND revision=? AND actor_id=?',
                (p.organization_id,view_id,revision,p.actor_id)).fetchone()
            if not grant or int(grant[0])!=meta['access_revision']:raise PublicationError('Case dataset not found',404)
        packet=None
        if verify:
            try:
                packet=CaseDataset.model_validate_json(row[0]).model_dump(mode='json')
                if content_digest(packet)!=row[1]:raise ValueError('digest')
            except ValueError:raise PublicationError('Stored case dataset failed its integrity check') from None
            parent=s.detail_in(c,p,meta,revision)
            if not self.matches(packet,parent):raise PublicationError('Case dataset no longer matches its analysis revision')
        return meta,packet,{'revision':revision,'content_sha256':row[1],'access_revision':int(row[2]),
            'parent_access_revision':meta['access_revision'],'published_at':row[3],
            'can_manage':meta['owner_actor_id']==p.actor_id and WRITE in p.scopes and 'org:analysis:write' in p.scopes,
            'source_independently_verified':False,'source_freshness':'unknown_source_not_shared','execution_authorized':False}

    @staticmethod
    def matches(packet,parent):
        a=parent['snapshot']
        return a['level']=='cases' and len(packet['cases'])==a['matching_cases'] and len(packet['cases'])==a['matching_rows'] and all(
            packet[k]==a[k] for k in ('source_content_sha256','analysis_spec_sha256','fingerprint_algorithm'))

    def attach(self,p,view_id,body):
        s=self.parent;s.scope(p,WRITE);s.scope(p,READ)
        if not body.user_confirmed or not body.case_sharing_confirmed:raise PublicationError('Explicit case-level sharing confirmation required',403)
        with s.transaction(p) as c:
            # Same organization admission lock used by parent publication quotas.
            s.q(c,'INSERT INTO gateway_analysis_orgs VALUES (?) ON CONFLICT DO NOTHING',(p.organization_id,))
            s.q(c,'SELECT organization_id FROM gateway_analysis_orgs WHERE organization_id=?'+(' FOR UPDATE' if s.db.is_postgres else ''),(p.organization_id,)).fetchone()
            meta=self.owner(c,p,view_id)
            if (body.revision,body.expected_parent_access_revision)!=(meta['revision'],meta['access_revision']):raise PublicationError('Analysis revision or access changed; reload')
            parent=s.detail_in(c,p,meta,body.revision)
            if parent['content_sha256']!=body.parent_content_sha256:raise PublicationError('Analysis content changed')
            packet=body.dataset.model_dump(mode='json')
            if not self.matches(packet,parent):raise PublicationError('Case source, analysis fingerprint or complete population differs from the parent analysis',422)
            if s.q(c,'SELECT 1 FROM gateway_analysis_case_data WHERE organization_id=? AND view_id=? AND revision=?',(p.organization_id,view_id,body.revision)).fetchone():raise PublicationError('This immutable revision already has case data; publish a new analysis revision')
            n=s.q(c,'SELECT count(*) FROM gateway_analysis_case_data WHERE organization_id=? AND deleted=0',(p.organization_id,)).fetchone()[0]
            if n>=MAX_ATTACHMENTS:raise PublicationError('Organization case attachment capacity reached; no history was pruned')
            digest=content_digest(packet)
            s.q(c,'INSERT INTO gateway_analysis_case_data (organization_id,view_id,revision,payload,content_sha256,access_revision,published_at) VALUES (?,?,?,?,?,1,?)',(p.organization_id,view_id,body.revision,json.dumps(packet),digest,s.now().isoformat()))
            s.audit(c,p,'analysis.cases.published',view_id=view_id,revision=body.revision,content_sha256=digest,cases=len(packet['cases']),events=sum(len(x['events']) for x in packet['cases']))
            return {'attached':True,'revision':body.revision,'content_sha256':digest}

    def access(self,p,view_id,revision,body=None):
        s=self.parent
        with s.transaction(p) as c:
            meta,_,info=self.load(c,p,view_id,revision,owner=True,verify=False)
            if body is not None:
                if not body.user_confirmed:raise PublicationError('Confirm the complete case-viewer list',403)
                self.current(info,body)
                current={r[0] for r in s.q(c,'SELECT actor_id FROM gateway_analysis_members WHERE organization_id=? AND view_id=?',(p.organization_id,view_id)).fetchall()}
                if any(m.actor_id not in current for m in body.members):raise PublicationError('Case viewers must already be explicit analysis members',422)
                s.q(c,'DELETE FROM gateway_analysis_case_grants WHERE organization_id=? AND view_id=? AND revision=?',(p.organization_id,view_id,revision))
                for m in body.members:s.q(c,'INSERT INTO gateway_analysis_case_grants VALUES (?,?,?,?,?)',(p.organization_id,view_id,revision,m.actor_id,meta['access_revision']))
                s.q(c,'UPDATE gateway_analysis_case_data SET access_revision=access_revision+1 WHERE organization_id=? AND view_id=? AND revision=?',(p.organization_id,view_id,revision))
                info['access_revision']+=1
                s.audit(c,p,'analysis.cases.access_changed',view_id=view_id,revision=revision,members=len(body.members),access_revision=info['access_revision'])
            rows=s.q(c,'SELECT actor_id,parent_access_revision FROM gateway_analysis_case_grants WHERE organization_id=? AND view_id=? AND revision=? ORDER BY actor_id',(p.organization_id,view_id,revision)).fetchall()
            return {**info,'members':[{'actor_id':a,'active':int(r)==meta['access_revision']} for a,r in rows]}

    @staticmethod
    def current(info,body):
        if (info['content_sha256'],info['access_revision'],info['parent_access_revision'])!=(body.content_sha256,body.expected_access_revision,body.expected_parent_access_revision):raise PublicationError('Case data or analysis access changed; reload')

    def delete(self,p,view_id,body):
        s=self.parent
        with s.transaction(p) as c:
            _,_,info=self.load(c,p,view_id,body.revision,owner=True,verify=False)
            if not body.user_confirmed:raise PublicationError('Confirm case dataset deletion',403)
            self.current(info,body)
            s.q(c,'DELETE FROM gateway_analysis_case_grants WHERE organization_id=? AND view_id=? AND revision=?',(p.organization_id,view_id,body.revision))
            s.q(c,"UPDATE gateway_analysis_case_data SET payload='',deleted=1,access_revision=access_revision+1 WHERE organization_id=? AND view_id=? AND revision=?",(p.organization_id,view_id,body.revision))
            s.audit(c,p,'analysis.cases.deleted',view_id=view_id,revision=body.revision)
            return {'deleted':True}

    def read(self,p,view_id,revision,*,activity='',variant='',after=0,limit=50,case_code=None,exporting=False):
        s=self.parent
        with s.transaction(p) as c:
            _,packet,info=self.load(c,p,view_id,revision)
            s.audit(c,p,'analysis.cases.exported' if exporting else 'analysis.cases.read',view_id=view_id,revision=revision,content_sha256=info['content_sha256'])
            if exporting:return {**info,'dataset':packet}
            if case_code is not None:
                case=next((x for x in packet['cases'] if x['code']==case_code),None)
                if case is None:raise PublicationError('Case not found',404)
                return {**info,'case':case,'codes_are_snapshot_local':True}
            return {**info,**analyze(packet,activity,variant,after,limit)}


def analyze(packet,activity='',variant='',after=0,limit=50):
    """Exact bounded descriptive queries over the single authorized immutable dataset."""
    limit=max(1,min(int(limit),100));after=max(0,int(after))
    cases=[];variants=Counter();nodes=Counter();edges=Counter();durations=[];rework=0
    for index,case in enumerate(packet['cases'],1):
        path=[x['activity'] for x in case['events']];key=content_digest(path)
        if (activity and activity not in path) or (variant and variant!=key):continue
        us=case['events'][-1]['elapsed_microseconds'];durations.append(us/1000000)
        repeated=len(set(path))<len(path);rework+=repeated;variants[tuple(path)]+=1
        nodes.update(path);edges.update(zip(path,path[1:]))
        cases.append({'case_number':index,'code':case['code'],'events':len(path),'duration_seconds':us/1000000,'rework':repeated,'variant':key})
    paths=sorted(variants.items(),key=lambda x:(-x[1],x[0]))
    selected=[x for x in cases if x['case_number']>after]
    return {'cases':selected[:limit],'next_after':selected[limit-1]['case_number'] if len(selected)>limit else None,
        'matching_cases':len(cases),'dataset_cases':len(packet['cases']),'matching_events':sum(nodes.values()),
        'rework_cases':rework,'median_duration_seconds':median(durations) if durations else None,
        'activities':[{'activity':a,'events':n} for a,n in sorted(nodes.items())],
        'transitions':[{'from':a,'to':b,'count':n} for (a,b),n in sorted(edges.items(),key=lambda x:(-x[1],x[0]))[:100]],
        'transition_types':len(edges),'transitions_truncated':len(edges)>100,
        'variants':[{'variant':content_digest(list(path)),'path':list(path),'cases':n} for path,n in paths[:20]],
        'variant_count':len(paths),'variants_truncated':len(paths)>20,
        'analysis_basis':'publisher_supplied_complete_coded_cohort','codes_are_snapshot_local':True,
        'elapsed_time_is_employee_effort':False,'cross_case_time_order_available':False}
