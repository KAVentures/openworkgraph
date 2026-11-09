# Domain-pack installation consistency

Installing or reconfiguring a domain pack now changes its metric library entries
and installation metadata in one DuckDB transaction under the existing workspace
lock. Uninstall uses the same boundary. The public API, mappings, report semantics
and owner-session permissions are unchanged.

Previously, metrics were individually committed before the installation record.
A full metric library or interrupted write could leave a partially installed pack,
or remove an earlier pack's metrics without replacing its metadata.

The capacity check counts user metrics plus the proposed replacement set, excluding
only currently present metrics owned by the previous installation. A successful
reconfiguration at the 50-metric limit remains possible. User metrics and other
pack installations stay untouched. New metrics use the existing saved-metric
schema and format. Any validation or storage error rolls back new/deleted metrics,
installation version, mapping, digest and history together.

Uninstall removes only extant IDs recorded as owned by this installation; missing
owned metrics do not prevent removal, and unrelated metrics are preserved.
Workspace locking serializes concurrent local install/uninstall requests; this
adds no remote multi-tenant store or cross-process locking contract.

Regression tests cover first-install capacity refusal, reconfiguration at capacity,
post-insertion failures halfway through metrics, failure after installation record
insertion, uninstall failure after metric deletion, exact prior-state restoration,
and concurrent installation. Fixtures and injected failures are synthetic. This
does not certify physical disk durability or backup recovery on every filesystem.
