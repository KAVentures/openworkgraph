# Assigning an existing employee to a provisioning provider

Ordinary SCIM User creation still refuses an existing roster identity. It never
matches an email to silently take over lifecycle authority, reactivate someone,
replace their actor identity, or transfer ownership from another provider.

For a company with an existing manual roster, a writable administrator can
explicitly assign one employee through **Identity → Identity provisioning →
Assign existing employee**. Enter the exact existing employee work email, optionally supply the provider's
external ID, choose **Review assignment**, then confirm that this provider may
deactivate and reactivate the employee. Read the displayed current employee
status and provider state before confirming. The existing writable-admin
boundary applies, including the explicitly configured recovery-token API;
provider service credentials and read-only administrators cannot assign people.

Assignment inserts a provider-to-employee linkage and minimized audit in one
transaction. The existing employee ID, email/evidence actor, SSO subject, display
name, active/offboarded status, manual teams, access generation, devices,
invitations and evidence are preserved. An inactive employee stays inactive.
A disabled provider may be prepared, but remains disabled until separately
enabled. A permanently revoked provider cannot receive assignments. No recording,
sharing, source-system access, or new employee/device consent is enabled.

After assignment, the provider's normal User lifecycle controls this employee.
Future deactivation uses the existing access-epoch/device/invitation/portal
revocation. Rehire never restores prior revoked credentials or manual teams
removed during offboarding. Group membership remains metadata; no SCIM Group is
mapped to a team, role, scope or evidence permission.

The exact-email preview carries its email in a bounded POST body rather than a URL and includes active or offboarded employees without listing the workforce. It resolves only within the chosen organization, then locks and checks the stable employee identity; confirmation uses that employee ID, never a guessed email match. The preview is a current-state check, not a reservation. Confirmation pins the
exact provider revision and an opaque keyed employee-state fingerprint, including
its access generation. Concurrent identity/SSO/status/epoch or provider changes
require a new review (412); another assignment or any existing provisioning
linkage, including a deleted User tombstone, refuses (409). A lost response can
mean assignment committed: read the provider's User at the known employee ID and
reconcile before retrying. Retrying is not a transfer or resurrection operation.
Audit failure or external-ID uniqueness failure rolls back the new linkage.

The existing admin API exposes:

```http
POST /v1/admin/scim-providers/{organization}/{provider}/assignment-preview
Content-Type: application/json

{"userName": "exact-existing-employee@example.test"}

GET /v1/admin/scim-providers/{organization}/{provider}/assignments/{employee_id}
POST /v1/admin/scim-providers/{organization}/{provider}/assignments/{employee_id}
Content-Type: application/json

{"revision": 2, "employeeVersion": "<opaque preview value>",
 "confirmAssignment": true, "externalId": "optional-provider-id"}
```

POST returns the existing employee's SCIM User (201), with full-resource ETag and
Location. External ID may be omitted/empty/null and otherwise follows the current
200-character scalar bounds. It is not an OIDC subject. Other identity attributes
cannot be changed through this operation. Missing/nonliteral confirmation,
invalid types, unknown/case-colliding fields, duplicate JSON, query options,
nonfinite/deep/malformed JSON and bodies over 64 KiB are refused. Credentials
remain in existing authorization headers, never assignment arguments or previews.
Raw SSO subjects, device/invitation credentials and supplied external IDs are not
included in the assignment audit.

The UI discards old previews after employee/external-ID edits, cancel, navigation,
provider reload and session changes. Stale confirmations require fresh review;
changing a selection while an older request is in flight cannot restore it.

Owned database/HTTP/OIDC/browser tests verify this bounded flow. They do not
certify customer IdP matching, live organization adoption or general ownership
migration. Renames, transfers, tombstone resurrection, broader SCIM compatibility
and company/provider rollout remain separate under #412/#370/#264.
