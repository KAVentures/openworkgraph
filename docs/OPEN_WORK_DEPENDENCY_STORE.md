# Durable declared case dependencies (experimental)

This opt-in local backend stores **owner-declared** case-to-prerequisite edges in
an explicit SQLite file. It does not read provider data, infer obligations,
authorize agents or claim an outcome. It is independent of the experimental
Stripe attempt-receipt branch (#675).

Call `declare_dependency(path, scope=..., actor=..., case_id=..., prerequisite=...)`
after the existing caller has established consent, authorization and source scope.
`dependency_context(...)` returns direct and transitive prerequisites and an
explicit unknown source/customer status. A concurrent SQLite write transaction
rejects cycles; traversal is bounded and reports truncation.

`remove_dependency` explicitly retracts an edge. The authorized lifecycle
caller **must** call `purge_actor` on consent withdrawal, account disconnection,
retention deletion or actor revocation. This experimental store is not connected
to the production REST permission lifecycle and must not be exposed as a new
unauthenticated endpoint. It never syncs or contacts external systems.

Acceptance: run `pytest -q tests/test_open_work_dependency_store.py`.
Production follow-up: wire it into the existing dashboard-session + source-scope
gates and case declaration lifecycle, with cross-store consistency and deletion
tests; keep historical evidence distinct from declared dependencies.

## Existing reviewed-declaration integration adapter

`replace_reviewed_dependencies(path, scope=..., actor=..., snapshot=...)`
atomically reconciles this local actor-scoped store against the existing
`open_work_case_graph.project_declared_case_graph` contract. It rejects
missing prerequisites, cycles, invalid provenance and overclaimed outcomes
**before writing**, removes stale edges, and leaves other actors untouched.
Only server-side code holding a freshly authenticated, consent/retention-filtered
`list_case_declarations` snapshot may call it. Do **not** accept snapshots
from a browser or MCP caller. The existing dashboard graph route remains the
authoritative review surface; this mirror grants no new source or agent rights.

Still required before production use: invoke reconciliation only inside the
existing source-scoped authenticated declaration lifecycle; bind the actor to
a real authorized session; purge on disconnection/revocation/retention; test
the cross-store failure/retry path and end-to-end browser/agent behavior.
