# Optional standalone User PATCH compatibility

Strict versions are the default for every provisioning provider. A writable
administrator can explicitly allow a missing `If-Match` **only on standalone
`PATCH /scim/v2/{provider_id}/Users/{id}`**. The public Okta SCIM2.0 guide shows
an unversioned pathless deactivation request; this option supports that bounded
request shape. It does not certify a complete Okta/Entra integration or accept
unsupported attributes/extensions. Optional [core names](SCIM_USER_NAMES.md) and [contact emails](SCIM_USER_EMAILS.md) have their own bounded contracts.

In **Identity → Identity provisioning → Client update policy**, review the current
policy and approve the overwrite behavior before enabling compatibility. Use
**Require strict versions** to disable it. This does not enable a disabled
provider. Revoked providers cannot change policy. Existing writable-admin and
configured recovery-token authorization applies; provider tokens and read-only
administrators cannot change it. A stale provider revision requires fresh review.
The UI discards cancelled/held/session-stale previews and clears confirmation
when reloading current state.

An unversioned PATCH applies its supported delta to the **current stored User**
inside the normal provider/employee transaction. Unmentioned attributes stay
current. There is no read-before-write retry or invented client baseline. A late,
reordered, or retried request may overwrite a newer human **or provider** change
to a targeted field, including active state. Choose this policy only when that
tradeoff is acceptable to the organization and test the actual IdP mappings.

Only an absent header qualifies. A supplied empty, stale, mismatched or wildcard
version is refused as before; duplicate If-Match headers are refused. A valid
supplied version still receives the exact normal CAS check. PUT, DELETE, Groups,
Bulk and direct service callers remain strict. Unknown operations/attributes,
queries, malformed/duplicate/nonfinite/deep JSON and existing input limits are
unchanged. A bad later PATCH operation rolls back the whole request.

Policy and provider current state are checked under the same provider-first lock
as User mutation. Disable/revoke/policy changes and PATCH serialize there; an
already committed request cannot be undone by a later disable, and subsequent
requests must observe the new state. Normal offboarding, device/invitation/portal
revocation, rehire protection, immutable userName/actor and consent/evidence
boundaries are unchanged. Neither policy nor provisioning enables recording,
sharing, source-system writes or SSO identity adoption.

Policy changes increment the existing provider revision and audit atomically.
An unchanged setting preserves its revision. Every accepted unversioned PATCH,
including a resource no-op, records a minimized use audit with employee ID and
policy revision. A no-op preserves User version/lastModified and does not repeat
offboarding. Audit failure rolls back the entire User/credential or policy change.

The admin API is `GET` / `PATCH`
`/v1/admin/scim-providers/{organization}/{provider_id}/client-policy`.

```json
{"revision": 2, "enabled": true, "confirmUnversioned": true}
```

Enable requires literal booleans and explicit confirmation. Disable uses
`{"revision": 3, "enabled": false}`. No query options are accepted. The read
response reports the current revision, policy boolean, provider enabled/revoked
state and `scope: "standalone_users_patch_only"`. Ordinary ServiceProviderConfig
continues advertising ETag support: supplied conditional versions are honored.

The additive configuration table defaults existing providers to strict without
rewriting identity rows. A current SQLite backup retains policy and provider
revision; restore requires normal schema initialization before serving requests.
An older backup's absent policy becomes strict. Existing warnings about restoring
older provisioning authority/credentials still apply. Older workers ignore this
new policy and keep requiring versions; mixed versions can therefore return428
for approved clients. Roll out compatible workers before enabling and validate
routing/health; the policy does not certify every worker or retrofit older code.

Owned database/HTTP/OIDC/browser fixtures verify these boundaries on SQLite and
PostgreSQL. Real customer IdP mappings, complex attributes, company rollout and
provider compatibility remain under #458/#412/#370/#264. Public references:
[RFC7644 versioning](https://www.rfc-editor.org/rfc/rfc7644.html#section-3.14) and
[Okta SCIM2.0](https://developer.okta.com/docs/api/openapi/okta-scim/guides/scim-20/).
