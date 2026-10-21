"""Organization process collaboration over existing append-only workflow knowledge.

Only explicitly published model data enters this namespace. No personal evidence,
source identifiers, private knowledge or connector access is enumerated here.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from shared.process_model import ProcessModel
from shared.workflow_knowledge import KnowledgeStore, KnowledgeWrite

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS gateway_shared_processes (
 organization_id TEXT NOT NULL, workflow_id TEXT NOT NULL, owner_actor_id TEXT NOT NULL,
 access_revision INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, PRIMARY KEY(organization_id, workflow_id));
CREATE TABLE IF NOT EXISTS gateway_process_members (
 organization_id TEXT NOT NULL, workflow_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 role TEXT NOT NULL, PRIMARY KEY(organization_id, workflow_id, actor_id));
CREATE TABLE IF NOT EXISTS gateway_process_authors (
 organization_id TEXT NOT NULL, workflow_id TEXT NOT NULL, revision INTEGER NOT NULL,
 author_actor_id TEXT NOT NULL, PRIMARY KEY(organization_id, workflow_id, revision));
CREATE TABLE IF NOT EXISTS gateway_process_reviews (
 organization_id TEXT NOT NULL, workflow_id TEXT NOT NULL, revision INTEGER NOT NULL,
 reviewer_actor_id TEXT NOT NULL, review_number INTEGER NOT NULL, decision TEXT NOT NULL,
 comment_text TEXT NOT NULL, content_sha256 TEXT NOT NULL, reviewed_at TEXT NOT NULL,
 PRIMARY KEY(organization_id, workflow_id, revision, reviewer_actor_id, review_number));
'''


class ProcessCollaborationError(ValueError):
    def __init__(self, detail: str, status: int = 409):
        super().__init__(detail)
        self.status = status


class RequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SharedProcessWrite(RequestModel):
    title: str = Field(min_length=1, max_length=240)
    process_model: ProcessModel
    expected_revision: Annotated[StrictInt, Field(ge=0)] = 0
    user_confirmed: StrictBool = False
    organization_sharing_confirmed: StrictBool = False


class ProcessMember(RequestModel):
    actor_id: str = Field(min_length=1, max_length=512)
    role: Literal['viewer', 'editor', 'reviewer']


class ProcessAccessWrite(RequestModel):
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    members: list[ProcessMember] = Field(default_factory=list, max_length=50)
    new_owner_actor_id: str | None = Field(default=None, min_length=1, max_length=512)
    user_confirmed: StrictBool = False


class ProcessReviewWrite(RequestModel):
    revision: Annotated[StrictInt, Field(ge=1)]
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_review_number: Annotated[StrictInt, Field(ge=0)] = 0
    decision: Literal['reviewed_design', 'changes_requested']
    comment: str = Field(default='', max_length=2000)
    user_confirmed: StrictBool = False


class ProcessArchiveWrite(RequestModel):
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    archived: StrictBool
    user_confirmed: StrictBool = False


class ProcessDeleteWrite(RequestModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    user_confirmed: StrictBool = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def namespace(organization_id: str) -> str:
    # Deliberately different from personal knowledge's [organization, actor].
    return json.dumps(['organization-process-catalog', organization_id], separators=(',', ':'))


class OrganizationProcessStore:
    def __init__(self, db):
        self.db = db
        self.knowledge = KnowledgeStore(db)

    def initialize(self):
        self.knowledge.initialize()
        with self.db.connect() as conn:
            for statement in _SCHEMA.split(';'):
                if statement.strip():
                    self.db._execute(conn, statement)

    @staticmethod
    def require_scope(p, scope):
        if scope not in p.scopes:
            raise ProcessCollaborationError('Missing process permission: ' + scope, 403)

    @contextmanager
    def write_transaction(self):
        with self.db.connect() as conn:
            if not self.db.is_postgres:
                conn.execute('BEGIN IMMEDIATE')
            yield conn

    def _meta(self, conn, p, workflow_id, *, lock=False):
        sql = '''SELECT owner_actor_id, access_revision, archived, created_at
                 FROM gateway_shared_processes WHERE organization_id = ? AND workflow_id = ?'''
        if lock and self.db.is_postgres:
            sql += ' FOR UPDATE'
        row = self.db._execute(conn, sql, (p.organization_id, workflow_id)).fetchone()
        if row is None:
            raise ProcessCollaborationError('Shared process not found', 404)
        return {'owner_actor_id': row[0], 'access_revision': int(row[1]),
                'archived': bool(row[2]), 'created_at': row[3]}

    def _role(self, conn, p, workflow_id, meta):
        if 'org:process:admin' in p.scopes:
            return 'admin'
        if meta['owner_actor_id'] == p.actor_id:
            return 'owner'
        row = self.db._execute(conn, '''SELECT role FROM gateway_process_members
            WHERE organization_id = ? AND workflow_id = ? AND actor_id = ?''',
            (p.organization_id, workflow_id, p.actor_id)).fetchone()
        return str(row[0]) if row else 'reader'

    def _record(self, conn, p, workflow_id, revision=None):
        sql = 'SELECT revision, payload FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?'
        params = [namespace(p.organization_id), workflow_id]
        if revision is not None:
            sql += ' AND revision = ?'
            params.append(revision)
        row = self.db._execute(conn, sql + ' ORDER BY revision DESC LIMIT 1', tuple(params)).fetchone()
        if row is None:
            raise ProcessCollaborationError('Shared process revision not found', 404)
        return json.loads(row[1]) | {'revision': int(row[0])}

    def _audit(self, conn, p, action, **details):
        self.db._execute(conn, '''INSERT INTO audit_log(observed_at, organization_id, principal_id,
            action, details_json) VALUES (?, ?, ?, ?, ?)''',
            (_now(), p.organization_id, p.audit_id, action, json.dumps(details)))

    def _review_state(self, conn, p, workflow_id, revision):
        # Count the latest statement from each reviewer; an old approval does not
        # override a later request for changes or a newer process revision.
        base = ''' FROM gateway_process_reviews r WHERE r.organization_id = ? AND r.workflow_id = ?
          AND r.revision = ? AND r.review_number = (SELECT MAX(v.review_number)
            FROM gateway_process_reviews v WHERE v.organization_id = r.organization_id
            AND v.workflow_id = r.workflow_id AND v.revision = r.revision
            AND v.reviewer_actor_id = r.reviewer_actor_id)'''
        params = (p.organization_id, workflow_id, revision)
        counts = self.db._execute(conn, 'SELECT decision, COUNT(*)' + base + ' GROUP BY decision', params).fetchall()
        totals = {row[0]: int(row[1]) for row in counts}
        status = ('changes_requested' if totals.get('changes_requested') else
                  'reviewed_design' if totals.get('reviewed_design') else 'needs_review')
        reviews = self.db._execute(conn, '''SELECT reviewer_actor_id, review_number, decision,
            comment_text, content_sha256, reviewed_at''' + base +
            ' ORDER BY reviewed_at DESC, reviewer_actor_id LIMIT 101', params).fetchall()
        return {'status': status, 'decision_counts': totals, 'reviews': [
            dict(zip(('reviewer_actor_id', 'review_number', 'decision', 'comment', 'content_sha256', 'reviewed_at'), row))
            for row in reviews[:100]], 'reviews_truncated': len(reviews) > 100,
            'review_is_business_policy_approval': False, 'execution_authorization': False,
            'reviewer_permission_is_historical_not_a_current_grant': True}

    def _detail(self, conn, p, workflow_id, *, revision=None):
        meta = self._meta(conn, p, workflow_id)
        record = self._record(conn, p, workflow_id, revision)
        author = self.db._execute(conn, '''SELECT author_actor_id FROM gateway_process_authors
            WHERE organization_id = ? AND workflow_id = ? AND revision = ?''',
            (p.organization_id, workflow_id, record['revision'])).fetchone()
        latest = self._record(conn, p, workflow_id)['revision']
        historical = record['revision'] != latest
        role = self._role(conn, p, workflow_id, meta)
        own_review = self.db._execute(conn, '''SELECT MAX(review_number) FROM gateway_process_reviews
            WHERE organization_id = ? AND workflow_id = ? AND revision = ? AND reviewer_actor_id = ?''',
            (p.organization_id, workflow_id, record['revision'], p.actor_id)).fetchone()[0] or 0
        members = self.db._execute(conn, '''SELECT actor_id, role FROM gateway_process_members
            WHERE organization_id = ? AND workflow_id = ? ORDER BY actor_id''',
            (p.organization_id, workflow_id)).fetchall()
        return {'process': record, 'organization_id': p.organization_id, **meta,
                'author_actor_id': author[0] if author else None, 'your_role': role,
                'your_actor_id': p.actor_id, 'your_review_number': int(own_review),
                'latest_revision': latest, 'viewing_historical_revision': historical,
                'permissions': {
                    'edit': not historical and not meta['archived'] and role in {'owner', 'editor', 'admin'} and 'org:process:write' in p.scopes,
                    'manage': role in {'owner', 'admin'} and 'org:process:write' in p.scopes,
                    'review': not historical and not meta['archived'] and role in {'owner', 'reviewer', 'admin'} and 'org:process:review' in p.scopes,
                    'transfer_owner': role == 'admin' and 'org:process:write' in p.scopes,
                },
                'members': [{'actor_id': row[0], 'role': row[1]} for row in members],
                'review': self._review_state(conn, p, workflow_id, record['revision']),
                'raw_evidence_rows_included': False, 'personal_knowledge_queried': False, 'shared_design_is_execution_authorization': False}

    def detail(self, p, workflow_id, *, revision=None):
        self.require_scope(p, 'org:process:read')
        with self.db.connect() as conn:
            result = self._detail(conn, p, workflow_id, revision=revision)
            self._audit(conn, p, 'process.read', workflow_id=workflow_id, revision=result['process']['revision'])
            return result

    def inventory(self, p, *, after='', limit=20, include_archived=False):
        self.require_scope(p, 'org:process:read')
        with self.db.connect() as conn:
            sql = '''SELECT workflow_id FROM gateway_shared_processes WHERE organization_id = ? AND workflow_id > ?'''
            if not include_archived:
                sql += ' AND archived = 0'
            cap = max(1, min(limit, 100))
            rows = self.db._execute(conn, sql + ' ORDER BY workflow_id LIMIT ?', (p.organization_id, after, cap + 1)).fetchall()
            items = []
            for row in rows[:cap]:
                detail = self._detail(conn, p, row[0])
                record = detail['process']
                items.append({key: record[key] for key in ('workflow_id', 'title', 'revision', 'content_sha256', 'saved_at')} | {
                    'owner_actor_id': detail['owner_actor_id'], 'archived': detail['archived'],
                    'review_status': detail['review']['status'], 'your_role': detail['your_role'],
                    'activity_count': len(record['process_model']['activities'])})
            self._audit(conn, p, 'process.inventory.read', returned=len(items))
        return {'processes': items, 'has_more': len(rows) > cap,
                'next_after': items[-1]['workflow_id'] if len(rows) > cap else None,
                'personal_knowledge_scanned': False, 'raw_evidence_scanned': False}

    def save(self, p, request: SharedProcessWrite, workflow_id=None):
        self.require_scope(p, 'org:process:read')
        self.require_scope(p, 'org:process:write')
        if not request.user_confirmed or not request.organization_sharing_confirmed:
            raise ProcessCollaborationError('Review the exact design and explicitly confirm organization sharing', 422)
        if not request.title.strip():
            raise ProcessCollaborationError('A nonblank process title is required', 422)
        if workflow_id is None and request.expected_revision != 0:
            raise ProcessCollaborationError('New processes require revision zero')
        creating = workflow_id is None
        workflow_id = workflow_id or 'process_' + uuid4().hex
        with self.write_transaction() as conn:
            if creating:
                self.db._execute(conn, '''INSERT INTO gateway_shared_processes
                    (organization_id, workflow_id, owner_actor_id, access_revision, archived, created_at)
                    VALUES (?, ?, ?, 1, 0, ?)''', (p.organization_id, workflow_id, p.actor_id, _now()))
            else:
                meta = self._meta(conn, p, workflow_id, lock=True)
                if meta['archived'] or self._role(conn, p, workflow_id, meta) not in {'owner', 'editor', 'admin'}:
                    raise ProcessCollaborationError('You cannot edit this shared process', 403)
            model = request.process_model
            saved = self.knowledge.save_in_transaction(conn, namespace(p.organization_id), KnowledgeWrite(
                workflow_id=workflow_id, title=request.title.strip(), procedure='Shared reviewed process: ' + model.goal,
                process_model=model, expected_revision=request.expected_revision, user_confirmed=True,
                source_client='OWG organization process review', evidence_refs=[],
                decision_rules=[r.text for r in model.business_rules if r.user_confirmed],
                unresolved_questions=[r.text for r in model.business_rules if not r.user_confirmed],
            ))
            self.db._execute(conn, '''INSERT INTO gateway_process_authors
                (organization_id, workflow_id, revision, author_actor_id) VALUES (?, ?, ?, ?)''',
                (p.organization_id, workflow_id, saved['revision'], p.actor_id))
            self._audit(conn, p, 'process.created' if creating else 'process.revision.saved',
                        workflow_id=workflow_id, revision=saved['revision'], content_sha256=saved['content_sha256'])
            return self._detail(conn, p, workflow_id)

    def set_access(self, p, workflow_id, request: ProcessAccessWrite):
        self.require_scope(p, 'org:process:read')
        self.require_scope(p, 'org:process:write')
        if not request.user_confirmed:
            raise ProcessCollaborationError('Explicit membership review is required', 422)
        members = [m.actor_id for m in request.members]
        if len(set(members)) != len(members) or any(actor != actor.strip() for actor in members):
            raise ProcessCollaborationError('Member identities must be unique, non-padding actor IDs', 422)
        with self.write_transaction() as conn:
            meta = self._meta(conn, p, workflow_id, lock=True)
            role = self._role(conn, p, workflow_id, meta)
            if role not in {'owner', 'admin'}:
                raise ProcessCollaborationError('Only the owner or a process administrator can change membership', 403)
            if meta['access_revision'] != request.expected_access_revision:
                raise ProcessCollaborationError('Membership changed; reload before editing access')
            new_owner = request.new_owner_actor_id or meta['owner_actor_id']
            if new_owner != meta['owner_actor_id'] and role != 'admin':
                raise ProcessCollaborationError('Process administrator permission is required to transfer ownership', 403)
            if not new_owner.strip() or new_owner != new_owner.strip() or new_owner in members:
                raise ProcessCollaborationError('Owner must be a distinct, non-padding actor identity', 422)
            self.db._execute(conn, '''UPDATE gateway_shared_processes SET owner_actor_id = ?, access_revision = ?
                WHERE organization_id = ? AND workflow_id = ?''',
                (new_owner, meta['access_revision'] + 1, p.organization_id, workflow_id))
            self.db._execute(conn, 'DELETE FROM gateway_process_members WHERE organization_id = ? AND workflow_id = ?', (p.organization_id, workflow_id))
            for member in request.members:
                self.db._execute(conn, '''INSERT INTO gateway_process_members
                    (organization_id, workflow_id, actor_id, role) VALUES (?, ?, ?, ?)''',
                    (p.organization_id, workflow_id, member.actor_id, member.role))
            self._audit(conn, p, 'process.access.changed', workflow_id=workflow_id,
                        access_revision=meta['access_revision'] + 1, member_count=len(members), owner_actor_id=new_owner)
            return self._detail(conn, p, workflow_id)

    def review(self, p, workflow_id, request: ProcessReviewWrite):
        self.require_scope(p, 'org:process:read')
        self.require_scope(p, 'org:process:review')
        if not request.user_confirmed:
            raise ProcessCollaborationError('Review this exact revision before submitting a decision', 422)
        with self.write_transaction() as conn:
            meta = self._meta(conn, p, workflow_id, lock=True)
            if meta['archived'] or self._role(conn, p, workflow_id, meta) not in {'owner', 'reviewer', 'admin'}:
                raise ProcessCollaborationError('You are not a reviewer for this process', 403)
            current = self._record(conn, p, workflow_id)
            if current['revision'] != request.revision or current['content_sha256'] != request.content_sha256:
                raise ProcessCollaborationError('Process revision changed; reload and review the current design')
            author = self.db._execute(conn, '''SELECT author_actor_id FROM gateway_process_authors
                WHERE organization_id = ? AND workflow_id = ? AND revision = ?''',
                (p.organization_id, workflow_id, request.revision)).fetchone()
            if request.decision == 'reviewed_design' and (not author or author[0] == p.actor_id):
                raise ProcessCollaborationError('A different colleague must approve the authored revision', 403)
            current_number = self.db._execute(conn, '''SELECT MAX(review_number) FROM gateway_process_reviews
                WHERE organization_id = ? AND workflow_id = ? AND revision = ? AND reviewer_actor_id = ?''',
                (p.organization_id, workflow_id, request.revision, p.actor_id)).fetchone()[0] or 0
            if current_number != request.expected_review_number:
                raise ProcessCollaborationError('Your review changed; reload before submitting another decision')
            self.db._execute(conn, '''INSERT INTO gateway_process_reviews (organization_id, workflow_id,
                revision, reviewer_actor_id, review_number, decision, comment_text, content_sha256, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (p.organization_id, workflow_id, request.revision,
                p.actor_id, current_number + 1, request.decision, request.comment, request.content_sha256, _now()))
            self._audit(conn, p, 'process.review.submitted', workflow_id=workflow_id, revision=request.revision,
                        review_number=current_number + 1, decision=request.decision)
            return self._detail(conn, p, workflow_id)

    def archive(self, p, workflow_id, request: ProcessArchiveWrite):
        self.require_scope(p, 'org:process:read')
        self.require_scope(p, 'org:process:write')
        if not request.user_confirmed:
            raise ProcessCollaborationError('Confirm this process archive change', 422)
        with self.write_transaction() as conn:
            meta = self._meta(conn, p, workflow_id, lock=True)
            if self._role(conn, p, workflow_id, meta) not in {'owner', 'admin'}:
                raise ProcessCollaborationError('Only the owner or a process administrator can archive', 403)
            if meta['access_revision'] != request.expected_access_revision:
                raise ProcessCollaborationError('Process access changed; reload')
            self.db._execute(conn, '''UPDATE gateway_shared_processes SET archived = ?, access_revision = ?
                WHERE organization_id = ? AND workflow_id = ?''',
                (int(request.archived), meta['access_revision'] + 1, p.organization_id, workflow_id))
            self._audit(conn, p, 'process.archive.changed', workflow_id=workflow_id, archived=request.archived)
            return self._detail(conn, p, workflow_id)

    def delete(self, p, workflow_id, request: ProcessDeleteWrite):
        self.require_scope(p, 'org:process:read')
        self.require_scope(p, 'org:process:write')
        if not request.user_confirmed:
            raise ProcessCollaborationError('Confirm deletion of all shared revisions and reviews', 422)
        with self.write_transaction() as conn:
            meta = self._meta(conn, p, workflow_id, lock=True)
            if self._role(conn, p, workflow_id, meta) not in {'owner', 'admin'}:
                raise ProcessCollaborationError('Only the owner or a process administrator can delete', 403)
            current = self._record(conn, p, workflow_id)
            if current['revision'] != request.expected_revision or meta['access_revision'] != request.expected_access_revision:
                raise ProcessCollaborationError('Process or access changed; reload before deleting')
            cursor = self.db._execute(conn, 'DELETE FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?', (namespace(p.organization_id), workflow_id))
            for table in ('gateway_process_reviews', 'gateway_process_authors', 'gateway_process_members', 'gateway_shared_processes'):
                self.db._execute(conn, f'DELETE FROM {table} WHERE organization_id = ? AND workflow_id = ?', (p.organization_id, workflow_id))
            self._audit(conn, p, 'process.deleted', workflow_id=workflow_id, versions_deleted=cursor.rowcount)
            return {'deleted': True, 'versions_deleted': cursor.rowcount, 'personal_knowledge_deleted': False}
