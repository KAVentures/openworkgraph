"""Versioned local grouping corrections on the existing reviewed-knowledge store.

Only exact execution references and human-declared labels persist. Grouping
never modifies capture, inferred executions, family clustering or source cases.
"""
from __future__ import annotations

import json
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from shared.execution_grouping import ExecutionGrouping, ExecutionID
from shared.workflow_knowledge import KnowledgeWrite
from .workflow_evidence import build_workflow_evidence

GROUP_ID=r'^group_[a-f0-9]{32}$'
GroupID=Annotated[str,Field(pattern=GROUP_ID)]
Revision=Annotated[StrictInt,Field(ge=0)]
MAX_GROUPS=100


class GroupingError(ValueError):
    pass


class GroupSave(BaseModel):
    model_config=ConfigDict(extra='forbid')
    workflow_id: GroupID | None=None
    expected_revision: Revision=0
    title: str=Field(min_length=1,max_length=120)
    execution_ids: list[ExecutionID]=Field(max_length=25)
    since: str | None=Field(default=None,max_length=64)
    until: str | None=Field(default=None,max_length=64)
    user_confirmed: StrictBool=False


class GroupMove(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id: GroupID
    source_revision: Annotated[StrictInt,Field(ge=1)]
    target_id: GroupID | None=None
    target_revision: Revision=0
    new_title: str | None=Field(default=None,min_length=1,max_length=120)
    execution_ids: list[ExecutionID]=Field(min_length=1,max_length=25)
    user_confirmed: StrictBool=False


def _verify(ids,*,since,until):
    if ids:
        # Strict selection rejects unavailable IDs, rather than silently keeping
        # a partial set from the bounded permitted source window.
        build_workflow_evidence(execution_ids=ids,since=since,until=until,
            source_event_limit=25000,max_runs=len(ids),max_events_per_run=1,include_canonical_evidence=False)


class WorkflowGroupings:
    def __init__(self,knowledge,*,owner='local',verify=_verify):
        self.knowledge=knowledge;self.db=knowledge.db;self.owner=owner;self.verify=verify

    def _record(self,conn,identifier):
        row=self.db._execute(conn,'''SELECT revision,payload FROM workflow_knowledge
            WHERE owner = ? AND workflow_id = ? ORDER BY revision DESC LIMIT 1''',(self.owner,identifier)).fetchone()
        if not row:raise LookupError('Reviewed workflow group not found')
        record=json.loads(row[1])|{'revision':int(row[0])}
        if not record.get('execution_grouping') or record.get('process_model'):
            raise LookupError('Reviewed workflow group not found')
        ExecutionGrouping.model_validate(record['execution_grouping'])
        return record

    def _records(self,conn):
        rows=self.db._execute(conn,'''SELECT k.workflow_id FROM workflow_knowledge k
            WHERE k.owner = ? AND k.workflow_id >= 'group_' AND k.workflow_id < 'group`'
            AND k.revision = (SELECT MAX(v.revision) FROM workflow_knowledge v
                WHERE v.owner = k.owner AND v.workflow_id = k.workflow_id)
            ORDER BY k.workflow_id LIMIT 101''',(self.owner,)).fetchall()
        if len(rows)>MAX_GROUPS:raise GroupingError('Group limit reached; remove an unused group before editing memberships')
        records=[]
        for row in rows:
            try:records.append(self._record(conn,row[0]))
            except LookupError:continue
        return records

    @staticmethod
    def _public(record):
        return {key:record[key] for key in ('workflow_id','title','revision','content_sha256','saved_at')}|{
            'execution_grouping':record['execution_grouping'],'data_layer':'user_reviewed_knowledge',
            'execution_authorization':False,'observed_evidence':False}

    @staticmethod
    def _check(record,expected):
        if record['revision']!=expected:raise GroupingError('Group changed; reload and review the exact proposed classification')

    @staticmethod
    def _ids(ids):
        return ExecutionGrouping(execution_ids=ids).execution_ids

    def _save(self,conn,identifier,title,ids,revision):
        if not title.strip():raise GroupingError('A nonblank group name is required')
        return self.knowledge.save_in_transaction(conn,self.owner,KnowledgeWrite(
            workflow_id=identifier,title=title.strip(),expected_revision=revision,user_confirmed=True,
            procedure='User-reviewed classification of selected inferred executions. Original evidence is unchanged. '
                'This is not verified business intent, case identity, a handoff, company policy or execution permission.',
            source_client='OWG reviewed workflow grouping',execution_grouping=ExecutionGrouping(execution_ids=ids)),
            allow_execution_grouping=True)

    def inventory(self):
        self.knowledge.initialize()
        with self.db.connect() as conn:
            return {'groups':[self._public(record) for record in self._records(conn)],
                    'limit':MAX_GROUPS,'maximum_executions_per_group':25,'canonical_families_modified':False}

    def get(self,identifier):
        self.knowledge.initialize()
        with self.db.connect() as conn:return self._public(self._record(conn,identifier))

    def save(self,request:GroupSave):
        if not request.user_confirmed:raise GroupingError('Review the exact label and selected executions before saving')
        if not request.title.strip():raise GroupingError('A nonblank group name is required')
        ids=self._ids(request.execution_ids)
        if not request.workflow_id and (request.expected_revision or not ids):
            raise GroupingError('A new group requires selected executions and revision zero')
        previous=self.get(request.workflow_id) if request.workflow_id else None
        if previous:self._check(previous,request.expected_revision)
        old_ids=set(previous['execution_grouping']['execution_ids']) if previous else set()
        added=[identifier for identifier in ids if identifier not in old_ids]
        if added:self.verify(added,since=request.since,until=request.until)
        self.knowledge.initialize()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            records=self._records(conn)
            identifier=request.workflow_id or 'group_'+uuid4().hex
            if previous:self._check(self._record(conn,identifier),request.expected_revision)
            elif len(records)>=MAX_GROUPS:raise GroupingError('Maximum 100 reviewed groups; delete an unused group first')
            for record in records:
                if record['workflow_id']!=identifier and set(record['execution_grouping']['execution_ids'])&set(ids):
                    raise GroupingError('An execution is assigned to another reviewed group; move or split it explicitly')
            return self._public(self._save(conn,identifier,request.title,ids,request.expected_revision))

    def move(self,request:GroupMove):
        if not request.user_confirmed:raise GroupingError('Review the exact source, target and selected memberships before moving')
        ids=self._ids(request.execution_ids)
        if request.target_id==request.source_id:raise GroupingError('Select a different target group')
        if (request.target_id is None)!=(request.new_title is not None):
            raise GroupingError('Choose an existing target or a new group name')
        if request.new_title is not None and not request.new_title.strip():
            raise GroupingError('A nonblank new group name is required')
        if request.target_id is None and request.target_revision:
            raise GroupingError('A new target requires revision zero')
        self.knowledge.initialize()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            records=self._records(conn)
            source=self._record(conn,request.source_id);self._check(source,request.source_revision)
            source_ids=source['execution_grouping']['execution_ids']
            if not set(ids)<=set(source_ids):raise GroupingError('Move only executions currently assigned to this source group')
            if request.target_id:
                target=self._record(conn,request.target_id);self._check(target,request.target_revision)
                target_ids=target['execution_grouping']['execution_ids'];target_id=request.target_id;title=target['title']
            else:
                if len(records)>=MAX_GROUPS:raise GroupingError('Maximum 100 reviewed groups; delete an unused group first')
                target_ids=[];target_id='group_'+uuid4().hex;title=request.new_title
            combined=list(dict.fromkeys(target_ids+ids))
            if len(combined)>25:raise GroupingError('The merged group would exceed 25 selected executions')
            # Both immutable versions commit together. Empty source groups keep
            # their history until the person explicitly deletes the group.
            source=self._save(conn,request.source_id,source['title'],[value for value in source_ids if value not in ids],request.source_revision)
            target=self._save(conn,target_id,title,combined,request.target_revision)
            return {'source':self._public(source),'target':self._public(target),'original_executions_modified':False}

    def forget(self,identifier,*,expected_revision,confirmed):
        if confirmed is not True:raise GroupingError('Explicit group deletion confirmation is required')
        self.knowledge.initialize()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record=self._record(conn,identifier);self._check(record,expected_revision)
            cursor=self.db._execute(conn,'DELETE FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?',
                                    (self.owner,identifier))
            return {'deleted':True,'versions_deleted':cursor.rowcount,'original_executions_modified':False}

    def history(self,identifier,*,before_revision=None,limit=20):
        self.get(identifier)
        page=self.knowledge.history(self.owner,identifier,before_revision=before_revision,limit=limit)
        return {'groups':[self._public(record) for record in page['workflows'] if record.get('execution_grouping') and not record.get('process_model')],
                'has_more':page['has_more'],'next_before_revision':page['next_before_revision']}
