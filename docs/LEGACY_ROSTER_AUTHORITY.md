# Legacy inactive-roster credential repair

Current offboarding already revokes the employee's device keys and personal
invitations atomically. Older inconsistent databases can still contain an
unrevoked key or invitation for an inactive employee. These rows must not regain
authority when that employee is explicitly rehired.

Roster startup now reconciles those **currently inactive** lifecycles. It locks
each employee, rechecks status, and revokes remaining device keys and personal
invitations in one transaction. Devices match the exact organization/actor or an
explicit employee-to-device binding. Roster timestamps, access generations,
teams, evidence, ownership and audit history are preserved. Repeated startup
leaves existing revocation timestamps unchanged. Schema creation commits before
repair so PostgreSQL schema/index locks do not block concurrent rehire. A failed
repair rolls back its credential changes; the server must complete startup
before serving traffic.

Manual, CSV/admin-API and SCIM rehire also revoke credentials remaining from the
inactive lifecycle **before activation**, within the same employee transaction.
Fresh approved invitations/enrollments remain available. If rehire wins a race
with startup, startup rechecks the locked active status and preserves the fresh
lifecycle's credentials. Current invitation/enrollment/offboarding locks remain
in effect.

Device authentication independently rejects a known inactive actor or explicit
inactive employee binding, even before repair runs. The core Gateway continues
to operate without a roster; it detects actual schema presence, caching only
presence so installation of the roster is seen later. This check does not infer
an employee from a guessed email or normalize a supplied identity into another.
Unregistered identities, unrelated organizations/active employees and separate
admin/integration credentials retain their existing controls. Human/portal
access retains the independent roster and access-generation checks.

This is a local persisted-state repair, not complete historical recovery. An
already active employee whose old offboarding history was erased cannot be
reconstructed from current status. Restoring an older backup can restore older
authority; there is no external anti-rollback ledger. Isolate restored databases
until current authority is reconciled or rotated. Disable company IdP accounts,
group enrollment grants and independent administrator/service credentials through
their respective controls.

Owned SQLite/PostgreSQL fixtures deliberately reconstruct missed old revocations;
they do not indicate that the fixed normal offboarding flow fails. Tests cover
startup and both rehire paths, rollback injection, pool behavior, concurrent fresh
rehire, unrelated authority and real HTTP refusal while retaining historical
evidence. They do not certify an actual customer database or IdP rollout.
