# Optional unique active Group names

Microsoft's [Entra SCIM endpoint guide](https://learn.microsoft.com/en-us/entra/identity/app-provisioning/use-scim-to-provision-users-and-groups)
requires unique Group `displayName` values for matching. That is an integration
requirement, not a general SCIM requirement. OWG therefore provides an explicit,
default-off policy per provisioning provider. It is not Entra certification.

In Identity provisioning, open **Group naming policy**, review the counts,
select **Require unique active Group names**, and save. Existing collisions
must be explicitly renamed or deleted through the provider before enabling.
The policy does not merge Groups, choose a winning identity, change membership,
or grant teams, login, observation, AI access or execution permissions.

Named administrators can GET/PATCH
`/v1/admin/scim-providers/{organization}/{provider_id}/group-name-policy`.
PATCH requires the current provider `revision` and an `enabled` JSON boolean.
Supplied stale revisions return 412. Revoked providers cannot change policy.
Query options and unknown fields are refused. Reports include only policy,
provider state, active Group count, and the number of additional Groups with
colliding names; they do not expose the names or member identities.

Names use the existing stored whitespace normalization and Python case folding,
including `Straße` / `STRASSE`. There is no new Unicode normalization or matching
filter operator. **Existing exact matching filters retain their behavior.**
Uniqueness does not itself establish full client matching interoperability.
While enabled, create, PUT and PATCH reject a colliding active name with
409/`uniqueness`, including Bulk operations. The current Group is excluded from
its own check. Deleted Group names can be reused through fresh Group IDs; old
membership or identity is never adopted. Bulk still commits each operation
separately; a later conflict does not undo an earlier successful operation.

Enable, create and rename checks hold the existing provider-first transaction
lock. SQLite uses BEGIN IMMEDIATE; PostgreSQL locks the provider row. Comparison
is bounded by the existing 100-Group provider-history limit and consistent across
the backends, without relying on their differing SQL lower/collation behavior.
No-op policy writes preserve revision/timestamps/audits. Changes increment the
provider revision and add a minimized audit in the same transaction. A failed
audit rolls back both. Group and User resource versions do not change merely
because an administrator changes this provider policy.

The additive policy table is included in native database backups. Current backup
restore preserves the flag and provider revision. A database predating the table
receives the default-off policy without rewriting Group history. **Restore is
not anti-rollback authority**: recheck the current policy before resuming an IdP
after any restore. All serving workers must be uniformly upgraded before this
policy is enabled; an older worker does not enforce the new constraint. A
corrupt policy or invalid retained naming metadata fails closed; it is not
silently interpreted as the policy being off.

The offline mapping preflight accepts optional declared
`provider_policy.unique_group_names: true`. That declaration removes the known
Entra name-uniqueness blocker only; it does not configure or verify the server.
Check the actual enabled policy, provider revision and worker versions. Static
success still requires a customer-controlled live test of real generated
requests. No OAuth/IdP connection, customer provisioning or source operation is
performed by the policy UI or preflight.
