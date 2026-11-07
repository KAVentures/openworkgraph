"""Local-first, actor-scoped durable case dependencies; no execution authority.

A dependency is an owner-declared edge, not observed causality or a business
outcome. The caller must authorize the actor and source scope before use.
"""
from __future__ import annotations

import os
import re
import sqlite3
import stat
from pathlib import Path

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_EDGES = 1000


def _valid(*values: str) -> None:
    if not all(isinstance(v, str) and _ID.fullmatch(v) and v not in (".", "..")
               for v in values):
        raise ValueError("invalid dependency scope or case identity")


def _connect(path: str) -> sqlite3.Connection:
    if not isinstance(path, str) or not path or path == ":memory:":
        raise ValueError("explicit durable path required")
    p = Path(path).expanduser()
    if p.is_symlink():
        raise ValueError("symlink database refused")
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never chmod an existing, potentially shared inode. Reject hard links,
    # special files and permissive files before SQLite opens the database.
    try:
        info = p.lstat()
    except FileNotFoundError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(p, flags, 0o600)
        except FileExistsError:
            pass  # Concurrent creator; inspect the resulting path below.
        else:
            os.close(fd)
        info = p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or p.is_symlink():
        raise ValueError("unsafe dependency database path")
    if os.name == "posix" and info.st_mode & 0o077:
        raise ValueError("dependency database permissions too broad")
    # An untrusted WAL/SHM sidecar could contain undeleted declarations.
    # This experimental store only supports SQLite's DELETE journal mode.
    for suffix in ("-wal", "-shm"):
        if Path(str(p) + suffix).exists() or Path(str(p) + suffix).is_symlink():
            raise ValueError("unsupported dependency database sidecar")
    db = sqlite3.connect(str(p), timeout=5)
    db.execute("PRAGMA busy_timeout=5000")
    if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
        db.close()
        raise ValueError("unsupported dependency database journal mode")
    db.execute("""CREATE TABLE IF NOT EXISTS declared_case_dependencies (
        scope TEXT NOT NULL, actor TEXT NOT NULL, case_id TEXT NOT NULL,
        prerequisite TEXT NOT NULL,
        PRIMARY KEY (scope, actor, case_id, prerequisite)
    )""")
    return db


def _edges(db: sqlite3.Connection, scope: str, actor: str) -> dict[str, set[str]]:
    rows = db.execute(
        "SELECT case_id, prerequisite FROM declared_case_dependencies WHERE scope=? AND actor=?",
        (scope, actor)).fetchall()
    graph: dict[str, set[str]] = {}
    for case_id, prerequisite in rows:
        graph.setdefault(case_id, set()).add(prerequisite)
    return graph


def declare_dependency(path: str, *, scope: str, actor: str,
                       case_id: str, prerequisite: str) -> bool:
    """Declare a prerequisite; reject cycles atomically across concurrent writers.

    Returns True for a new edge and False for an already-present edge.
    """
    _valid(scope, actor, case_id, prerequisite)
    if case_id == prerequisite:
        raise ValueError("self dependency")
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        graph = _edges(db, scope, actor)
        if prerequisite in graph.get(case_id, set()):
            return False
        if sum(map(len, graph.values())) >= _MAX_EDGES:
            raise ValueError("dependency graph capacity reached")
        stack, seen = [prerequisite], set()
        while stack:
            current = stack.pop()
            if current == case_id:
                raise ValueError("dependency cycle")
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph.get(current, ()))
        db.execute(
            "INSERT INTO declared_case_dependencies(scope,actor,case_id,prerequisite) VALUES (?,?,?,?)",
            (scope, actor, case_id, prerequisite))
        return True


def remove_dependency(path: str, *, scope: str, actor: str,
                      case_id: str, prerequisite: str) -> bool:
    """Explicitly remove an owner-declared edge; never delete source evidence."""
    _valid(scope, actor, case_id, prerequisite)
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        return bool(db.execute(
            "DELETE FROM declared_case_dependencies WHERE scope=? AND actor=? AND case_id=? AND prerequisite=?",
            (scope, actor, case_id, prerequisite)).rowcount)


def dependency_context(path: str, *, scope: str, actor: str,
                       case_id: str, limit: int = 100) -> dict:
    """Return bounded direct/transitive prerequisites, not inferred blockers.

    Whether prerequisites are *actually blocked* requires separately authorized,
    fresh source status. No status or customer outcome is fabricated here.
    """
    _valid(scope, actor, case_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid result limit")
    with _connect(path) as db:
        graph = _edges(db, scope, actor)
    direct = sorted(graph.get(case_id, ()))
    visited: set[str] = set()
    frontier = list(reversed(direct))
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        if len(visited) > limit:
            return {"case_id": case_id, "direct": direct[:limit],
                    "prerequisites": sorted(visited)[:limit], "truncated": True,
                    "source_status": "unknown", "customer_outcome": "unknown"}
        frontier.extend(sorted(graph.get(node, ()), reverse=True))
    return {"case_id": case_id, "direct": direct,
            "prerequisites": sorted(visited), "truncated": False,
            "source_status": "unknown", "customer_outcome": "unknown"}


def purge_actor(path: str, *, scope: str, actor: str) -> int:
    """Caller must invoke on consent withdrawal, deletion or scope revocation."""
    _valid(scope, actor)
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        return db.execute(
            "DELETE FROM declared_case_dependencies WHERE scope=? AND actor=?",
            (scope, actor)).rowcount

def reconcile_current_reviewed_dependencies(
    path: str, *, scope: str, actor: str,
    scope_provider=None, snapshot_provider=None,
) -> dict:
    """Fail-closed read-through mirror of current retained, reviewed declarations.

    Internal operator adapter only: callers must derive actor from an authenticated
    local session. The provider defaults deliberately use the same consent/retention
    and current Stripe account scope gates as the dashboard declaration routes.
    No caller-supplied snapshot is accepted. Never expose this directly as a
    public endpoint or agent authority.
    """
    from .open_work_case_declarations import (
        CaseDeclarationError, list_case_declarations,
    )
    from .open_work_declaration_routes import _source_scope

    _valid(scope, actor)
    current_scope = scope_provider or _source_scope
    current_snapshot = snapshot_provider or list_case_declarations

    # The scoped provider must agree before AND after reading the reviewed
    # declaration snapshot. On disconnect/replacement, purge this actor's
    # mirror rather than retain a stale prerequisite graph.
    if current_scope() != scope:
        purge_actor(path, scope=scope, actor=actor)
        raise CaseDeclarationError("source scope changed or disconnected")
    snapshot = current_snapshot(scope)
    if current_scope() != scope:
        purge_actor(path, scope=scope, actor=actor)
        raise CaseDeclarationError("source scope changed during review")
    result = replace_reviewed_dependencies(
        path, scope=scope, actor=actor, snapshot=snapshot)
    if current_scope() != scope:
        purge_actor(path, scope=scope, actor=actor)
        raise CaseDeclarationError("source scope changed during reconciliation")
    # The review ledger may have changed while SQLite was being updated.
    # Require a stable, currently eligible snapshot; if not, erase the
    # mirror instead of reporting a stale graph as current.
    latest = current_snapshot(scope)
    if latest != snapshot or current_scope() != scope:
        purge_actor(path, scope=scope, actor=actor)
        raise CaseDeclarationError("reviewed declarations changed during reconciliation")
    return {**result, "current_review_rechecked": True,
            "agent_execution_authorized": False}


def replace_reviewed_dependencies(
    path: str, *, scope: str, actor: str, snapshot: dict,
) -> dict:
    """Atomically mirror an already-authorized reviewed declaration graph.

    The caller must obtain the snapshot through the authenticated declaration
    API with current consent and retention. Never accept raw client-supplied
    edges as reviewed facts; this is an internal integration adapter.
    Deletions remove stale edges within the actor's scope.
    """
    from .open_work_case_graph import project_declared_case_graph

    _valid(scope, actor)
    graph = project_declared_case_graph(snapshot)
    if graph["source_state"] != "unknown_until_explicit_recheck":
        raise ValueError("unexpected source state")
    desired = set()
    for edge in graph["edges"]:
        dependent = edge["dependent_ref"]
        prerequisite = edge["prerequisite_ref"]
        _valid(dependent, prerequisite)
        if edge["edge_type"] != "owner_declared_dependency" or edge["source_verified"] is not False:
            raise ValueError("untrusted dependency provenance")
        desired.add((dependent, prerequisite))
    if len(desired) > _MAX_EDGES:
        raise ValueError("dependency graph capacity reached")
    # BEGIN IMMEDIATE serializes concurrent writers. No partial replacement on
    # malformed snapshots, SQLite errors, or a failed transaction.
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = set(db.execute(
            "SELECT case_id, prerequisite FROM declared_case_dependencies WHERE scope=? AND actor=?",
            (scope, actor)).fetchall())
        for case_id, prerequisite in sorted(existing - desired):
            db.execute(
                "DELETE FROM declared_case_dependencies WHERE scope=? AND actor=? AND case_id=? AND prerequisite=?",
                (scope, actor, case_id, prerequisite))
        for case_id, prerequisite in sorted(desired - existing):
            db.execute(
                "INSERT INTO declared_case_dependencies(scope,actor,case_id,prerequisite) VALUES (?,?,?,?)",
                (scope, actor, case_id, prerequisite))
    return {
        "reviewed_case_count": graph["case_count"],
        "reviewed_dependency_count": len(desired),
        "removed_stale_edges": len(existing - desired),
        "added_reviewed_edges": len(desired - existing),
        "source_status": "unknown",
        "customer_outcome": "unknown",
        "agent_execution_authorized": False,
    }
