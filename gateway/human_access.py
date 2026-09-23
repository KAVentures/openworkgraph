from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Query

from shared.evidence import rich_evidence_row
from shared.time_utils import normalize_timestamp
from .db import GatewayDB
from .lifecycle import effective_since
from .query import workflow_trace

_TEAM_SCOPE_RE = re.compile(r"^team:([A-Za-z0-9._-]{1,128}):evidence:read$")
_ALLOWED_STATIC_SCOPES = {
    "self:evidence:read",
    "org:evidence:read",
    "aggregate:read",
    "pseudonymous:evidence:read",
}
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_actor_teams (
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (organization_id, actor_id, team_id)
);
CREATE INDEX IF NOT EXISTS idx_gateway_actor_teams_team ON gateway_actor_teams(organization_id, team_id, actor_id);
"""
_POSTGRES_SCHEMA = _SQLITE_SCHEMA


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    return int(raw) if raw else default


def _valid_scope(scope: str) -> bool:
    return scope in _ALLOWED_STATIC_SCOPES or bool(_TEAM_SCOPE_RE.match(scope))


def _normalize_group_map(value: str) -> dict[str, frozenset[str]]:
    if not value.strip():
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("OWG_GATEWAY_OIDC_GROUP_SCOPE_MAP must be a JSON object")
    result: dict[str, frozenset[str]] = {}
    for group, scopes in parsed.items():
        if not isinstance(scopes, list):
            raise ValueError(f"OIDC group mapping for {group!r} must be a list")
        normalized = frozenset(str(x).strip() for x in scopes if str(x).strip())
        invalid = sorted(scope for scope in normalized if not _valid_scope(scope))
        if invalid:
            raise ValueError(f"invalid human access scopes for group {group!r}: {invalid}")
        result[str(group)] = normalized
    return result


@dataclass(frozen=True)
class HumanAccessSettings:
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    organization_claim: str = "owg_org"
    actor_claim: str = "sub"
    groups_claim: str = "groups"
    group_scope_map: dict[str, frozenset[str]] | None = None
    self_read_enabled: bool = True
    aggregate_min_actors: int = 5
    pseudonym_key: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.audience and self.jwks_url)

    @classmethod
    def from_env(cls) -> "HumanAccessSettings":
        group_map = _normalize_group_map(os.getenv("OWG_GATEWAY_OIDC_GROUP_SCOPE_MAP", ""))
        value = cls(
            issuer=str(os.getenv("OWG_GATEWAY_OIDC_ISSUER", "") or "").strip(),
            audience=str(os.getenv("OWG_GATEWAY_OIDC_AUDIENCE", "") or "").strip(),
            jwks_url=str(os.getenv("OWG_GATEWAY_OIDC_JWKS_URL", "") or "").strip(),
            organization_claim=str(os.getenv("OWG_GATEWAY_OIDC_ORGANIZATION_CLAIM", "owg_org") or "owg_org").strip(),
            actor_claim=str(os.getenv("OWG_GATEWAY_OIDC_ACTOR_CLAIM", "sub") or "sub").strip(),
            groups_claim=str(os.getenv("OWG_GATEWAY_OIDC_GROUPS_CLAIM", "groups") or "groups").strip(),
            group_scope_map=group_map,
            self_read_enabled=_bool_env("OWG_GATEWAY_OIDC_SELF_READ", True),
            aggregate_min_actors=_int_env("OWG_GATEWAY_AGGREGATE_MIN_ACTORS", 5),
            pseudonym_key=str(os.getenv("OWG_GATEWAY_PSEUDONYM_KEY", "") or ""),
        )
        configured = [bool(value.issuer), bool(value.audience), bool(value.jwks_url)]
        if any(configured) and not all(configured):
            raise ValueError("OIDC requires issuer, audience and JWKS URL together")
        if value.aggregate_min_actors < 3:
            raise ValueError("OWG_GATEWAY_AGGREGATE_MIN_ACTORS must be at least 3")
        wants_pseudonymous = any(
            "pseudonymous:evidence:read" in scopes
            for scopes in (value.group_scope_map or {}).values()
        )
        if wants_pseudonymous and len(value.pseudonym_key) < 32:
            raise ValueError("pseudonymous human access requires OWG_GATEWAY_PSEUDONYM_KEY of at least 32 characters")
        return value


@dataclass(frozen=True)
class HumanPrincipal:
    subject: str
    organization_id: str
    actor_id: str
    groups: frozenset[str]
    scopes: frozenset[str]
    issuer: str

    @property
    def audit_id(self) -> str:
        digest = hashlib.sha256(f"{self.issuer}|{self.subject}".encode("utf-8")).hexdigest()[:24]
        return f"oidc:{digest}"

    @property
    def team_ids(self) -> frozenset[str]:
        values: set[str] = set()
        for scope in self.scopes:
            match = _TEAM_SCOPE_RE.match(scope)
            if match:
                values.add(match.group(1))
        return frozenset(values)


class OIDCVerifier:
    """Verify externally issued OIDC JWTs without changing service-token auth.

    PyJWT is imported lazily so local-only and SQLite development installs do not
    need the optional Gateway identity dependency unless OIDC is configured.
    """

    def __init__(self, settings: HumanAccessSettings) -> None:
        self.settings = settings
        self._client: Any = None

    def verify(self, token: str) -> dict[str, Any]:
        if not self.settings.enabled:
            raise ValueError("OIDC human access is not configured")
        try:
            import jwt
            from jwt import PyJWKClient
        except ImportError as exc:
            raise RuntimeError("OIDC Gateway access requires `pip install '.[gateway]'`") from exc
        if self._client is None:
            self._client = PyJWKClient(self.settings.jwks_url, cache_keys=True)
        signing_key = self._client.get_signing_key_from_jwt(token)
        return dict(
            jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=30,
                options={"require": ["exp", "sub"]},
            )
        )


def _claim(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in [x for x in str(path).split(".") if x]:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def principal_from_claims(settings: HumanAccessSettings, payload: dict[str, Any]) -> HumanPrincipal:
    subject = str(payload.get("sub") or "").strip()
    organization_id = str(_claim(payload, settings.organization_claim) or "").strip()
    actor_id = str(_claim(payload, settings.actor_claim) or "").strip()
    raw_groups = _claim(payload, settings.groups_claim)
    if isinstance(raw_groups, str):
        groups = frozenset([raw_groups])
    elif isinstance(raw_groups, list):
        groups = frozenset(str(x) for x in raw_groups if str(x))
    else:
        groups = frozenset()
    if not subject or not organization_id or not actor_id:
        raise ValueError("OIDC token must resolve subject, organization and actor identity")
    if max(len(subject), len(organization_id), len(actor_id)) > 512:
        raise ValueError("OIDC identity claims exceed 512 characters")
    scopes: set[str] = set()
    if settings.self_read_enabled:
        scopes.add("self:evidence:read")
    for group in groups:
        scopes.update((settings.group_scope_map or {}).get(group, frozenset()))
    return HumanPrincipal(
        subject=subject,
        organization_id=organization_id,
        actor_id=actor_id,
        groups=groups,
        scopes=frozenset(scopes),
        issuer=settings.issuer,
    )


def init_human_access_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        if db.is_postgres:
            for statement in [x.strip() for x in _POSTGRES_SCHEMA.split(";") if x.strip()]:
                conn.execute(statement)
        else:
            conn.executescript(_SQLITE_SCHEMA)


def set_actor_teams(db: GatewayDB, organization_id: str, actor_id: str, teams: list[str]) -> list[str]:
    org = str(organization_id or "").strip()
    actor = str(actor_id or "").strip()
    if not org or not actor:
        raise ValueError("organization_id and actor_id are required")
    normalized = sorted({str(team).strip() for team in teams if str(team).strip()})
    invalid = [team for team in normalized if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", team)]
    if invalid:
        raise ValueError(f"invalid team identifiers: {invalid}")
    with db.connect() as conn:
        db._execute(conn, "DELETE FROM gateway_actor_teams WHERE organization_id = ? AND actor_id = ?", (org, actor))
        for team in normalized:
            db._execute(
                conn,
                "INSERT INTO gateway_actor_teams(organization_id, actor_id, team_id, updated_at) VALUES (?, ?, ?, ?)",
                (org, actor, team, normalize_timestamp(__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())),
            )
    return normalized


def actor_teams(db: GatewayDB, organization_id: str, actor_id: str) -> set[str]:
    with db.connect() as conn:
        cur = db._execute(
            conn,
            "SELECT team_id FROM gateway_actor_teams WHERE organization_id = ? AND actor_id = ? ORDER BY team_id",
            (organization_id, actor_id),
        )
        rows = cur.fetchall()
    return {str(row[0]) for row in rows}


def _pseudonym(key: str, organization_id: str, kind: str, value: str) -> str:
    digest = hmac.new(
        key.encode("utf-8"),
        f"{organization_id}|{kind}|{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"{kind}_{digest}"


def _authorize_actor(db: GatewayDB, principal: HumanPrincipal, requested_actor: str | None) -> tuple[str, str]:
    target = str(requested_actor or principal.actor_id).strip()
    if target == principal.actor_id and "self:evidence:read" in principal.scopes:
        return target, "self"
    if "org:evidence:read" in principal.scopes:
        return target, "organization"
    permitted_teams = principal.team_ids
    if permitted_teams and actor_teams(db, principal.organization_id, target) & set(permitted_teams):
        return target, "team"
    raise PermissionError("human principal is not authorized to read this actor")


def _aggregate_patterns(
    db: GatewayDB,
    *,
    organization_id: str,
    since: str | None,
    until: str | None,
    min_actors: int,
    limit: int,
) -> dict[str, Any]:
    effective = effective_since(db, organization_id, since)
    clauses = ["organization_id = ?", "actor_id <> ''"]
    params: list[Any] = [organization_id]
    if effective:
        clauses.append("observed_at >= ?")
        params.append(normalize_timestamp(effective))
    if until:
        clauses.append("observed_at <= ?")
        params.append(normalize_timestamp(until))
    where = " AND ".join(clauses)
    with db.connect() as conn:
        cur = db._execute(
            conn,
            f"SELECT COUNT(DISTINCT actor_id) AS actors FROM evidence_events WHERE {where}",
            tuple(params),
        )
        cohort_row = cur.fetchone()
        cohort = int(cohort_row[0] if cohort_row else 0)
        if cohort < min_actors:
            return {
                "organization_id": organization_id,
                "minimum_actors": min_actors,
                "cohort_actor_count": cohort,
                "suppressed": True,
                "patterns": [],
            }
        params2 = list(params) + [min_actors, max(1, min(int(limit), 200))]
        cur2 = db._execute(
            conn,
            f"""SELECT event_type, COALESCE(app, '') AS app,
                       COUNT(*) AS events,
                       COUNT(DISTINCT actor_id) AS actors,
                       COALESCE(SUM(duration_seconds), 0) AS duration_seconds
                FROM evidence_events
                WHERE {where}
                GROUP BY event_type, COALESCE(app, '')
                HAVING COUNT(DISTINCT actor_id) >= ?
                ORDER BY actors DESC, events DESC, event_type ASC
                LIMIT ?""",
            tuple(params2),
        )
        rows = cur2.fetchall()
        columns = [d[0] for d in cur2.description] if cur2.description else None
    patterns = [db._row(row, columns) for row in rows]
    return {
        "organization_id": organization_id,
        "minimum_actors": min_actors,
        "cohort_actor_count": cohort,
        "suppressed": False,
        "patterns": patterns,
        "actor_identifiers_returned": False,
        "note": "Thresholded aggregation reduces disclosure risk but is not a claim of anonymization.",
    }


def install_human_access(
    app: FastAPI,
    *,
    db: GatewayDB,
    settings: HumanAccessSettings,
    require_admin: Callable[[str | None], None],
    verifier: Any | None = None,
) -> None:
    app.state.human_access_settings = settings
    app.state.human_oidc_verifier = verifier or OIDCVerifier(settings)

    @app.on_event("startup")
    def init_access_schema() -> None:
        init_human_access_schema(db)

    def human(authorization: str | None = Header(default=None)) -> HumanPrincipal:
        if not settings.enabled:
            raise HTTPException(status_code=503, detail="OIDC human access is not configured")
        raw = str(authorization or "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
        if not token:
            raise HTTPException(status_code=401, detail="OIDC bearer token required")
        try:
            claims = app.state.human_oidc_verifier.verify(token)
            return principal_from_claims(settings, claims)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="valid OIDC bearer token required") from exc

    @app.get("/v1/human/me")
    def human_me(p: HumanPrincipal = __import__("fastapi").Depends(human)) -> dict[str, Any]:
        return {
            "organization_id": p.organization_id,
            "actor_id": p.actor_id,
            "principal_id": p.audit_id,
            "scopes": sorted(p.scopes),
            "team_ids": sorted(p.team_ids),
            "raw_subject_exposed": False,
        }

    @app.put("/v1/admin/access/{organization_id}/actors/{actor_id}/teams")
    def admin_set_actor_teams(
        organization_id: str,
        actor_id: str,
        teams: list[str],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        try:
            values = set_actor_teams(db, organization_id, actor_id, teams)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(
            organization_id=organization_id,
            principal_id="gateway-admin",
            action="human_access.actor_teams.updated",
            details={"actor_id": actor_id, "teams": values},
        )
        return {"organization_id": organization_id, "actor_id": actor_id, "teams": values}

    @app.get("/v1/admin/access/{organization_id}/actors/{actor_id}/teams")
    def admin_get_actor_teams(
        organization_id: str,
        actor_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        values = sorted(actor_teams(db, organization_id, actor_id))
        return {"organization_id": organization_id, "actor_id": actor_id, "teams": values}

    @app.get("/v1/human/workflow-trace")
    def human_trace(
        actor_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cursor: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
        p: HumanPrincipal = __import__("fastapi").Depends(human),
    ) -> dict[str, Any]:
        try:
            target, mode = _authorize_actor(db, p, actor_id)
            payload = workflow_trace(
                db,
                organization_id=p.organization_id,
                actor_id=target,
                since=since,
                until=until,
                cursor=cursor,
                limit=limit,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.audit_id,
            action="human_access.trace.read",
            details={"actor_id": target, "access_mode": mode, "returned": payload.get("returned", 0)},
        )
        payload["human_access_mode"] = mode
        return payload

    @app.get("/v1/human/pseudonymous/workflow-trace")
    def pseudonymous_trace(
        since: str | None = None,
        until: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
        p: HumanPrincipal = __import__("fastapi").Depends(human),
    ) -> dict[str, Any]:
        if "pseudonymous:evidence:read" not in p.scopes:
            raise HTTPException(status_code=403, detail="missing required scope: pseudonymous:evidence:read")
        if len(settings.pseudonym_key) < 32:
            raise HTTPException(status_code=503, detail="pseudonymous access key is not configured")
        try:
            payload = workflow_trace(
                db,
                organization_id=p.organization_id,
                since=since,
                until=until,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for row in payload.get("rows") or []:
            for field, kind in (("actor_id", "actor"), ("device_id", "device"), ("session_id", "session")):
                value = str(row.get(field) or "")
                row[field] = _pseudonym(settings.pseudonym_key, p.organization_id, kind, value) if value else ""
        payload["pseudonymized"] = True
        payload["pseudonymization_is_anonymization"] = False
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.audit_id,
            action="human_access.pseudonymous_trace.read",
            details={"returned": payload.get("returned", 0)},
        )
        return payload

    @app.get("/v1/human/aggregate/patterns")
    def aggregate_patterns(
        since: str | None = None,
        until: str | None = None,
        min_actors: int | None = Query(default=None, ge=3, le=1000),
        limit: int = Query(default=50, ge=1, le=200),
        p: HumanPrincipal = __import__("fastapi").Depends(human),
    ) -> dict[str, Any]:
        if "aggregate:read" not in p.scopes:
            raise HTTPException(status_code=403, detail="missing required scope: aggregate:read")
        k = max(settings.aggregate_min_actors, int(min_actors or settings.aggregate_min_actors))
        try:
            payload = _aggregate_patterns(
                db,
                organization_id=p.organization_id,
                since=since,
                until=until,
                min_actors=k,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.audit_id,
            action="human_access.aggregate.read",
            details={"minimum_actors": k, "patterns": len(payload.get("patterns") or []), "suppressed": payload.get("suppressed")},
        )
        return payload
