"""Explicit aggregate publications with deny-by-default view access and bounded retention."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from shared.analysis_snapshot import Snapshot, Strict, content_digest

SCHEMA = '''
CREATE TABLE IF NOT EXISTS gateway_analysis_orgs (organization_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS gateway_analysis_views (
 organization_id TEXT NOT NULL, view_id TEXT NOT NULL, owner_actor_id TEXT NOT NULL,
 revision INTEGER NOT NULL, access_revision INTEGER NOT NULL, expires_at TEXT NOT NULL,
 PRIMARY KEY (organization_id, view_id));
CREATE INDEX IF NOT EXISTS idx_analysis_expiry ON gateway_analysis_views(organization_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_analysis_global_expiry ON gateway_analysis_views(expires_at, organization_id, view_id);
CREATE INDEX IF NOT EXISTS idx_analysis_owner ON gateway_analysis_views(organization_id, owner_actor_id, view_id);
CREATE TABLE IF NOT EXISTS gateway_analysis_revisions (
 organization_id TEXT NOT NULL, view_id TEXT NOT NULL, revision INTEGER NOT NULL,
 title TEXT NOT NULL, payload TEXT NOT NULL, content_sha256 TEXT NOT NULL, published_at TEXT NOT NULL,
 publisher_actor_id TEXT NOT NULL, PRIMARY KEY(organization_id, view_id, revision));
CREATE TABLE IF NOT EXISTS gateway_analysis_members (
 organization_id TEXT NOT NULL, view_id TEXT NOT NULL, actor_id TEXT NOT NULL, role TEXT NOT NULL,
 PRIMARY KEY(organization_id, view_id, actor_id));
CREATE INDEX IF NOT EXISTS idx_analysis_member ON gateway_analysis_members(organization_id, actor_id, view_id);
'''
MAX_VIEWS = 100
MAX_REVISIONS = 10


class PublicationError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Publish(Strict):
    title: Annotated[str, Field(min_length=1, max_length=240)]
    snapshot: Snapshot
    expected_revision: Annotated[StrictInt, Field(ge=0)] = 0
    expires_in_days: Annotated[StrictInt, Field(ge=1, le=90)] = 7
    user_confirmed: StrictBool = False
    organization_sharing_confirmed: StrictBool = False

    @field_validator('title')
    @classmethod
    def valid_unicode(cls, value):
        value.encode('utf-8')
        return value


class Member(Strict):
    actor_id: Annotated[str, Field(min_length=1, max_length=512)]
    role: Literal['viewer', 'editor']

    @field_validator('actor_id')
    @classmethod
    def valid_unicode(cls, value):
        value.encode('utf-8')
        return value


class Access(Strict):
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    members: list[Member] = Field(default_factory=list, max_length=50)
    expires_in_days: Annotated[StrictInt, Field(ge=1, le=90)] | None = None
    user_confirmed: StrictBool = False

    @model_validator(mode='after')
    def unique(self):
        if len({m.actor_id for m in self.members}) != len(self.members):
            raise ValueError('duplicate member')
        return self


class Delete(Strict):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    user_confirmed: StrictBool = False


class AnalysisPublications:
    def __init__(self, db, *, now=None):
        self.db = db
        self.now = now or (lambda: datetime.now(timezone.utc))

    def initialize(self):
        with self.db.connect() as c:
            for statement in SCHEMA.split(';'):
                if statement.strip(): self.q(c, statement)
            from .analysis_case_data import initialize
            initialize(self,c)

    def q(self, c, sql, params=()):
        return self.db._execute(c, sql, tuple(params))

    @staticmethod
    def scope(p, name):
        if name not in p.scopes: raise PublicationError('Missing analysis permission: '+name, 403)

    def audit(self, c, p, action, **details):
        self.q(c, 'INSERT INTO audit_log(observed_at, organization_id, principal_id, action, details_json) VALUES (?, ?, ?, ?, ?)',
               (self.now().isoformat(), p.organization_id, p.audit_id, action, json.dumps(details)))

    def erase(self, c, org, view_id):
        from .analysis_case_data import erase
        erase(self,c,org,view_id)
        for table in ('gateway_analysis_members', 'gateway_analysis_revisions', 'gateway_analysis_views'):
            self.q(c, f'DELETE FROM {table} WHERE organization_id=? AND view_id=?', (org, view_id))

    def purge_expired(self, organization_id=None):
        with self.db.connect() as c:
            if not self.db.is_postgres: c.execute('BEGIN IMMEDIATE')
            sql = 'SELECT organization_id, view_id FROM gateway_analysis_views WHERE expires_at<=?'
            params = [self.now().isoformat()]
            if organization_id is not None:
                sql += ' AND organization_id=?'
                params.append(organization_id)
            sql += ' ORDER BY expires_at, organization_id, view_id LIMIT 100'
            if self.db.is_postgres: sql += ' FOR UPDATE SKIP LOCKED'
            rows = self.q(c, sql, params).fetchall()
            counts = {}
            for org, view_id in rows:
                self.erase(c, org, view_id)
                counts[org] = counts.get(org, 0)+1
            for org, count in counts.items():
                self.q(c, 'INSERT INTO audit_log(observed_at, organization_id, principal_id, action, details_json) VALUES (?, ?, ?, ?, ?)',
                       (self.now().isoformat(), org, 'system:analysis-retention', 'analysis.expired', json.dumps({'publications':count})))
            return len(rows)

    @contextmanager
    def transaction(self, p):
        self.scope(p, 'org:analysis:read')
        # Separate commit: even a later 404 must not roll back expiry cleanup.
        self.purge_expired(p.organization_id)
        with self.db.connect() as c:
            if not self.db.is_postgres: c.execute('BEGIN IMMEDIATE')
            yield c

    def meta(self, c, p, view_id):
        lock = ' FOR UPDATE' if self.db.is_postgres else ''
        row = self.q(c, 'SELECT owner_actor_id, revision, access_revision, expires_at FROM gateway_analysis_views '
                     'WHERE organization_id=? AND view_id=? AND expires_at>?'+lock, (p.organization_id, view_id, self.now().isoformat())).fetchone()
        if not row: raise PublicationError('Shared analysis not found', 404)
        owner, revision, access_revision, expiry = row
        role = 'admin' if 'org:analysis:admin' in p.scopes else 'owner' if owner == p.actor_id else None
        if role is None:
            member = self.q(c, 'SELECT role FROM gateway_analysis_members WHERE organization_id=? AND view_id=? AND actor_id=?',
                            (p.organization_id, view_id, p.actor_id)).fetchone()
            role = member[0] if member else None
        if role is None: raise PublicationError('Shared analysis not found', 404)
        return {'view_id': view_id, 'owner_actor_id': owner, 'revision': int(revision),
                'access_revision': int(access_revision), 'expires_at': expiry, 'your_role': role}

    def detail_in(self, c, p, meta, revision=None):
        revision = meta['revision'] if revision is None else revision
        if type(revision) is not int or not 1 <= revision <= MAX_REVISIONS:
            raise PublicationError('Shared analysis not found',404)
        row = self.q(c, 'SELECT title, payload, content_sha256, published_at, publisher_actor_id FROM gateway_analysis_revisions '
                     'WHERE organization_id=? AND view_id=? AND revision=?', (p.organization_id, meta['view_id'], revision)).fetchone()
        if not row: raise PublicationError('Shared analysis not found', 404)
        title, payload, digest, at, author = row
        try:
            snapshot = Snapshot.model_validate_json(payload).model_dump(mode='json')
            if content_digest(snapshot) != digest:
                raise ValueError('digest mismatch')
        except ValueError:
            raise PublicationError('Stored publication failed its integrity check; contact its owner') from None
        manage = meta['your_role'] in {'owner', 'admin'} and 'org:analysis:write' in p.scopes
        return {**meta, 'viewing_revision': revision, 'title': title, 'snapshot': snapshot,
                'content_sha256': digest, 'published_at': at, 'publisher_actor_id': author,
                'permissions': {'edit': revision == meta['revision'] and 'org:analysis:write' in p.scopes and meta['your_role'] in {'owner','admin','editor'}, 'manage': manage},
                'source_freshness': 'unknown_source_not_shared', 'source_independently_verified': False,
                'execution_authorized': False}

    def inventory(self, p, after='', limit=20):
        limit = max(1, min(int(limit), 100))
        with self.transaction(p) as c:
            rows = self.q(c, '''SELECT v.view_id, v.revision, v.expires_at, r.title, r.content_sha256
                FROM gateway_analysis_views v JOIN gateway_analysis_revisions r ON r.organization_id=v.organization_id
                  AND r.view_id=v.view_id AND r.revision=v.revision
                WHERE v.organization_id=? AND v.view_id>? AND v.expires_at>? AND (v.owner_actor_id=? OR ?=1 OR EXISTS
                 (SELECT 1 FROM gateway_analysis_members m WHERE m.organization_id=v.organization_id
                  AND m.view_id=v.view_id AND m.actor_id=?)) ORDER BY v.view_id LIMIT ?''',
                (p.organization_id, after, self.now().isoformat(), p.actor_id, int('org:analysis:admin' in p.scopes), p.actor_id, limit+1)).fetchall()
            return {'views': [dict(zip(('view_id','revision','expires_at','title','content_sha256'), row)) for row in rows[:limit]],
                    'can_publish': 'org:analysis:write' in p.scopes,
                    'next_after': rows[limit-1][0] if len(rows)>limit else None}

    def publish(self, p, body: Publish, view_id=None):
        self.scope(p, 'org:analysis:write')
        if not body.user_confirmed or not body.organization_sharing_confirmed:
            raise PublicationError('Explicit review and organization sharing confirmation required', 403)
        snapshot = body.snapshot.model_dump(mode='json')
        digest = content_digest(snapshot)
        with self.transaction(p) as c:
            if view_id is None:
                if body.expected_revision != 0: raise PublicationError('New publication requires revision zero')
                # Serialize quota admission per organization on both backends.
                self.q(c, 'INSERT INTO gateway_analysis_orgs VALUES (?) ON CONFLICT DO NOTHING', (p.organization_id,))
                self.q(c, 'SELECT organization_id FROM gateway_analysis_orgs WHERE organization_id=?'+
                       (' FOR UPDATE' if self.db.is_postgres else ''), (p.organization_id,)).fetchone()
                total = self.q(c, 'SELECT count(*) FROM gateway_analysis_views WHERE organization_id=?', (p.organization_id,)).fetchone()[0]
                if total >= MAX_VIEWS: raise PublicationError('Organization publication capacity reached')
                view_id = 'ana_'+uuid4().hex
                expiry = (self.now()+timedelta(days=body.expires_in_days)).isoformat()
                self.q(c, 'INSERT INTO gateway_analysis_views VALUES (?, ?, ?, 0, 1, ?)', (p.organization_id, view_id, p.actor_id, expiry))
            meta = self.meta(c, p, view_id)
            if meta['your_role'] not in {'owner','admin','editor'}: raise PublicationError('Analysis editor permission required', 403)
            if meta['revision'] != body.expected_revision: raise PublicationError('Analysis revision changed; reload before publishing')
            if meta['revision'] >= MAX_REVISIONS: raise PublicationError('Publication retains at most ten revisions; no history was removed')
            revision = meta['revision']+1
            self.q(c, 'INSERT INTO gateway_analysis_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                   (p.organization_id, view_id, revision, body.title, json.dumps(snapshot), digest, self.now().isoformat(), p.actor_id))
            self.q(c, 'UPDATE gateway_analysis_views SET revision=? WHERE organization_id=? AND view_id=?', (revision, p.organization_id, view_id))
            self.audit(c, p, 'analysis.published', view_id=view_id, revision=revision, content_sha256=digest)
            meta['revision'] = revision
            return self.detail_in(c, p, meta)

    def detail(self, p, view_id, revision=None, *, exporting=False):
        with self.transaction(p) as c:
            meta = self.meta(c, p, view_id)
            result = self.detail_in(c, p, meta, revision)
            self.audit(c, p, 'analysis.exported' if exporting else 'analysis.read', view_id=view_id,
                       revision=result['viewing_revision'], content_sha256=result['content_sha256'])
            return result

    def preflight(self, p, view_id, manage=False):
        self.scope(p, 'org:analysis:write')
        with self.transaction(p) as c:
            meta = self.meta(c, p, view_id)
            allowed = {'owner','admin'} if manage else {'owner','admin','editor'}
            if meta['your_role'] not in allowed:
                raise PublicationError('Analysis owner permission required' if manage else 'Analysis editor permission required',403)

    def access(self, p, view_id, body: Access | None = None):
        with self.transaction(p) as c:
            meta = self.meta(c, p, view_id)
            if meta['your_role'] not in {'owner','admin'} or 'org:analysis:write' not in p.scopes:
                raise PublicationError('Analysis owner permission required', 403)
            if body is not None:
                if not body.user_confirmed: raise PublicationError('Explicit access-change confirmation required', 403)
                if meta['access_revision'] != body.expected_access_revision: raise PublicationError('Analysis access changed; reload')
                if any(m.actor_id==meta['owner_actor_id'] for m in body.members): raise PublicationError('Owner access is implicit')
                self.q(c, 'DELETE FROM gateway_analysis_members WHERE organization_id=? AND view_id=?', (p.organization_id, view_id))
                for m in body.members:
                    self.q(c, 'INSERT INTO gateway_analysis_members VALUES (?, ?, ?, ?)', (p.organization_id, view_id, m.actor_id, m.role))
                expiry = (self.now()+timedelta(days=body.expires_in_days)).isoformat() if body.expires_in_days is not None else meta['expires_at']
                self.q(c, 'UPDATE gateway_analysis_views SET access_revision=access_revision+1, expires_at=? WHERE organization_id=? AND view_id=?',
                       (expiry, p.organization_id, view_id))
                meta['access_revision'] += 1
                meta['expires_at'] = expiry
                self.audit(c, p, 'analysis.access_changed', view_id=view_id, access_revision=meta['access_revision'], members=len(body.members))
            members = self.q(c, 'SELECT actor_id, role FROM gateway_analysis_members WHERE organization_id=? AND view_id=? ORDER BY actor_id', (p.organization_id, view_id)).fetchall()
            return {**meta, 'members': [{'actor_id': a, 'role': r} for a,r in members]}

    def delete(self, p, view_id, body: Delete):
        self.scope(p, 'org:analysis:write')
        with self.transaction(p) as c:
            meta = self.meta(c, p, view_id)
            if meta['your_role'] not in {'owner','admin'}: raise PublicationError('Analysis owner permission required',403)
            if not body.user_confirmed: raise PublicationError('Explicit deletion confirmation required',403)
            if (body.expected_revision,body.expected_access_revision)!=(meta['revision'],meta['access_revision']):
                raise PublicationError('Analysis or access changed; reload before deletion')
            self.erase(c,p.organization_id,view_id)
            self.audit(c,p,'analysis.deleted',view_id=view_id)
            return {'deleted':True,'view_id':view_id}
