"""Versioned collaborative definitions bound to exact, separately authorized case data."""
import json
from typing import Annotated
from uuid import uuid4

from pydantic import Field, StrictBool, StrictInt, field_validator
from shared.analysis_snapshot import Strict, content_digest
from shared.analysis_case_dataset import Digest
from shared.analysis_case_query import Query, execute
from .analysis_publications import PublicationError

SCHEMA='''CREATE TABLE IF NOT EXISTS gateway_analysis_case_queries (
 organization_id TEXT NOT NULL, view_id TEXT NOT NULL, parent_revision INTEGER NOT NULL,
 query_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, author_actor_id TEXT NOT NULL, saved_at TEXT NOT NULL,
 PRIMARY KEY(organization_id,view_id,parent_revision,query_id,revision))'''
MAX_QUERIES=20
MAX_REVISIONS=10


def initialize(parent,c):parent.q(c,SCHEMA)


def erase(parent,c,org,view_id,revision=None):
    sql='DELETE FROM gateway_analysis_case_queries WHERE organization_id=? AND view_id=?';params=[org,view_id]
    if revision is not None:sql+=' AND parent_revision=?';params.append(revision)
    parent.q(c,sql,params)


class Basis(Strict):
    parent_revision: Annotated[StrictInt,Field(ge=1,le=10)]
    dataset_content_sha256: Digest


class Save(Basis):
    expected_parent_access_revision: Annotated[StrictInt,Field(ge=1)]
    expected_case_access_revision: Annotated[StrictInt,Field(ge=1)]
    expected_revision: Annotated[StrictInt,Field(ge=0)] = 0
    name: Annotated[str,Field(min_length=1,max_length=120)]
    spec: Query
    user_confirmed: StrictBool = False

    @field_validator('name')
    @classmethod
    def name_text(cls,value):
        value=value.strip();value.encode('utf-8')
        if not value:raise ValueError('name is empty')
        return value


class Evaluate(Basis):
    spec: Query


class Remove(Basis):
    expected_revision: Annotated[StrictInt,Field(ge=1)]
    user_confirmed: StrictBool = False


class Saved(Strict):
    format: str = 'openworkgraph.saved-case-analysis.v1'
    dataset_content_sha256: Digest
    name: Annotated[str,Field(min_length=1,max_length=120)]
    spec: Query

    @field_validator('format')
    @classmethod
    def version(cls,value):
        if value!='openworkgraph.saved-case-analysis.v1':raise ValueError('unsupported saved analysis')
        return value

    @field_validator('name')
    @classmethod
    def unicode(cls,value):value.encode('utf-8');return value


class CaseQueries:
    def __init__(self,cases):self.cases=cases;self.parent=cases.parent

    def context(self,c,p,view_id,revision,*,writing=False,owner=False):
        meta,packet,info=self.cases.load(c,p,view_id,revision)
        if writing:
            self.parent.scope(p,'org:analysis:write')
            if meta['your_role'] not in {'owner','editor','admin'}:raise PublicationError('Analysis editor permission required',403)
        if owner and meta['owner_actor_id']!=p.actor_id:raise PublicationError('Only the publication owner can delete saved analyses',403)
        return meta,packet,info

    def read_preflight(self,p,view_id,revision):
        with self.parent.transaction(p) as c:self.context(c,p,view_id,revision)

    def preflight(self,p,view_id,revision,*,owner=False):
        with self.parent.transaction(p) as c:self.context(c,p,view_id,revision,writing=True,owner=owner)

    @staticmethod
    def basis(info,body):
        if info['content_sha256']!=body.dataset_content_sha256:raise PublicationError('Case dataset changed; reload')

    def evaluate(self,p,view_id,body):
        s=self.parent
        with s.transaction(p) as c:
            _,packet,info=self.context(c,p,view_id,body.parent_revision)
            self.basis(info,body);result=execute(packet,body.spec)
            s.audit(c,p,'analysis.query.evaluated',view_id=view_id,revision=body.parent_revision,
                content_sha256=info['content_sha256'],spec_sha256=result['spec_sha256'])
            return {**info,**result}

    def inventory(self,p,view_id,parent_revision):
        s=self.parent
        with s.transaction(p) as c:
            meta,_,info=self.context(c,p,view_id,parent_revision)
            rows=s.q(c,'''SELECT q.query_id,q.revision,q.payload,q.content_sha256,q.author_actor_id,q.saved_at
                FROM gateway_analysis_case_queries q WHERE q.organization_id=? AND q.view_id=? AND q.parent_revision=?
                 AND q.revision=(SELECT MAX(v.revision) FROM gateway_analysis_case_queries v
                    WHERE v.organization_id=q.organization_id AND v.view_id=q.view_id AND v.parent_revision=q.parent_revision AND v.query_id=q.query_id)
                ORDER BY q.query_id LIMIT ?''',(p.organization_id,view_id,parent_revision,MAX_QUERIES+1)).fetchall()
            if len(rows)>MAX_QUERIES:raise PublicationError('Stored saved analysis capacity exceeded')
            items=[]
            for row in rows:
                try:items.append(self.item(row,info))
                except PublicationError:
                    # Keep only authorized maintenance identifiers; never return corrupt content.
                    items.append({'query_id':row[0],'revision':int(row[1]),'name':'Unavailable: integrity check failed','integrity_ok':False})
            return {**info,'queries':items,'can_write':'org:analysis:write' in p.scopes and meta['your_role'] in {'owner','editor','admin'},
                'can_delete':'org:analysis:write' in p.scopes and meta['owner_actor_id']==p.actor_id}

    @staticmethod
    def item(row,info):
        query_id,revision,payload,digest,author,at=row
        try:
            value=Saved.model_validate_json(payload).model_dump(mode='json')
            if value['dataset_content_sha256']!=info['content_sha256'] or content_digest(value)!=digest:raise ValueError('digest')
        except ValueError:raise PublicationError('Stored saved analysis failed its integrity check') from None
        return {**value,'integrity_ok':True,'query_id':query_id,'revision':int(revision),'content_sha256':digest,'author_actor_id':author,'saved_at':at}

    def get(self,c,p,view_id,parent_revision,query_id,info,revision=None):
        sql='SELECT query_id,revision,payload,content_sha256,author_actor_id,saved_at FROM gateway_analysis_case_queries WHERE organization_id=? AND view_id=? AND parent_revision=? AND query_id=?'
        params=[p.organization_id,view_id,parent_revision,query_id]
        if revision is not None:sql+=' AND revision=?';params.append(revision)
        row=self.parent.q(c,sql+' ORDER BY revision DESC LIMIT 1',params).fetchone()
        if not row:raise PublicationError('Saved analysis not found',404)
        return self.item(row,info)

    def detail(self,p,view_id,parent_revision,query_id,revision=None,*,exporting=False):
        s=self.parent
        with s.transaction(p) as c:
            _,packet,info=self.context(c,p,view_id,parent_revision)
            item=self.get(c,p,view_id,parent_revision,query_id,info,revision)
            latest=s.q(c,'SELECT MAX(revision) FROM gateway_analysis_case_queries WHERE organization_id=? AND view_id=? AND parent_revision=? AND query_id=?',(p.organization_id,view_id,parent_revision,query_id)).fetchone()[0]
            result=execute(packet,item['spec'])
            s.audit(c,p,'analysis.query.exported' if exporting else 'analysis.query.read',view_id=view_id,revision=parent_revision,
                query_id=query_id,query_revision=item['revision'],content_sha256=item['content_sha256'])
            return {**info,'query':item,'latest_revision':int(latest),'result':result,'execution_authorized':False}

    def save(self,p,view_id,body,query_id=None):
        s=self.parent
        if not body.user_confirmed:raise PublicationError('Explicit review of shared analysis/check metadata required',403)
        with s.transaction(p) as c:
            _,_,info=self.context(c,p,view_id,body.parent_revision,writing=True)
            self.basis(info,body)
            if (info['parent_access_revision'],info['access_revision'])!=(body.expected_parent_access_revision,body.expected_case_access_revision):raise PublicationError('Case or parent access changed; reload before sharing')
            if query_id is None:
                if body.expected_revision!=0:raise PublicationError('A new saved analysis requires revision zero')
                n=s.q(c,'SELECT count(DISTINCT query_id) FROM gateway_analysis_case_queries WHERE organization_id=? AND view_id=? AND parent_revision=?',
                    (p.organization_id,view_id,body.parent_revision)).fetchone()[0]
                if n>=MAX_QUERIES:raise PublicationError('Dataset saved-analysis capacity reached; nothing was pruned')
                query_id='qry_'+uuid4().hex;latest=0
            else:
                row=s.q(c,'SELECT MAX(revision) FROM gateway_analysis_case_queries WHERE organization_id=? AND view_id=? AND parent_revision=? AND query_id=?',
                    (p.organization_id,view_id,body.parent_revision,query_id)).fetchone();latest=int(row[0] or 0)
                if not latest:raise PublicationError('Saved analysis not found',404)
            if latest!=body.expected_revision:raise PublicationError('Saved analysis changed; reload')
            if latest>=MAX_REVISIONS:raise PublicationError('Saved analyses retain at most ten revisions; nothing was pruned')
            saved=Saved(name=body.name,spec=body.spec,dataset_content_sha256=info['content_sha256']).model_dump(mode='json');digest=content_digest(saved)
            s.q(c,'INSERT INTO gateway_analysis_case_queries VALUES (?,?,?,?,?,?,?,?,?)',
                (p.organization_id,view_id,body.parent_revision,query_id,latest+1,json.dumps(saved),digest,p.actor_id,s.now().isoformat()))
            s.audit(c,p,'analysis.query.saved',view_id=view_id,revision=body.parent_revision,query_id=query_id,query_revision=latest+1,content_sha256=digest)
            return {'query_id':query_id,'revision':latest+1,'content_sha256':digest}

    def delete(self,p,view_id,query_id,body):
        s=self.parent
        with s.transaction(p) as c:
            _,_,info=self.context(c,p,view_id,body.parent_revision,writing=True,owner=True)
            self.basis(info,body)
            if not body.user_confirmed:raise PublicationError('Confirm deletion of every saved-analysis revision',403)
            latest=s.q(c,'SELECT MAX(revision) FROM gateway_analysis_case_queries WHERE organization_id=? AND view_id=? AND parent_revision=? AND query_id=?',
                (p.organization_id,view_id,body.parent_revision,query_id)).fetchone()[0]
            if latest!=body.expected_revision:raise PublicationError('Saved analysis changed or was deleted; reload')
            s.q(c,'DELETE FROM gateway_analysis_case_queries WHERE organization_id=? AND view_id=? AND parent_revision=? AND query_id=?',
                (p.organization_id,view_id,body.parent_revision,query_id))
            s.audit(c,p,'analysis.query.deleted',view_id=view_id,revision=body.parent_revision,query_id=query_id)
            return {'deleted':True}
