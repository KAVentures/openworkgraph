# Enterprise declared-policy signing and distribution

OpenWorkGraph can distribute an organization's declared workflow policy/SOP to enrolled endpoints without making the Gateway itself the policy authority.

This feature is **opt-in**. Existing local-only policy files, local policy proposals, human/agent capture, procedural memory, MCP, and evidence synchronization keep their existing behavior unless managed declared policy is explicitly enabled in endpoint configuration.

## Trust roles

The design separates three roles:

1. **Policy signing administrator** — holds the Ed25519 private signing key and signs validated declared-policy manifests offline.
2. **Gateway administrator** — may publish/select already-signed bundles for an organization, but the Gateway does not need or store the private signing key and does not claim signature authenticity.
3. **Enrolled endpoint** — has only a device credential plus one or more pinned organization public keys. It verifies the signature, organization binding and revision before accepting a bundle.

A Gateway administrator therefore cannot create organizational policy merely by possessing the Gateway admin token. An invalid or unsigned publication can disrupt availability, but a correctly configured endpoint rejects it.

## Signature format

Bundles use:

- schema `1.0`;
- Ed25519 signatures;
- canonical UTF-8 JSON with sorted object keys and no insignificant whitespace;
- SHA-256 manifest and bundle identities;
- explicit `organization_id`;
- monotonic integer `policy_revision`;
- `key_id` for planned key rotation;
- timezone-aware canonical `issued_at`.

The signed bytes cover the organization, revision, issue time, key ID, algorithm, manifest SHA and complete manifest.

## Generate an offline signing keypair

Keep the private key on a policy-administration machine rather than on ordinary endpoints or the Gateway:

```bash
python -m server.enterprise_policy_admin keygen \
  --key-id acme-policy-2026 \
  --private-key ./private/acme-policy-2026.pem \
  --public-key ./public/acme-policy-2026.json
```

The command refuses to overwrite existing files and does not print the private key. The private PEM is written with restrictive local permissions where the platform supports them.

The generated public document is safe to distribute to managed endpoints. The current CLI uses an unencrypted PKCS#8 private PEM protected by filesystem access controls; hardware-backed/HSM signing is not claimed by this implementation.

## Sign a declared-policy manifest

The input must already satisfy the normal OpenWorkGraph declared-policy schema:

```bash
python -m server.enterprise_policy_admin sign ./declared-policies.json \
  --organization-id acme \
  --revision 17 \
  --key-id acme-policy-2026 \
  --private-key ./private/acme-policy-2026.pem \
  --output ./signed/acme-policy-r17.json
```

Verify it independently before publication:

```bash
python -m server.enterprise_policy_admin verify ./signed/acme-policy-r17.json \
  --organization-id acme \
  --public-key ./public/acme-policy-2026.json
```

## Publish to a self-hosted Gateway

Publication uses the existing Gateway administrator credential and transports the signed JSON unchanged:

```text
PUT /v1/admin/declared-policy/acme
Authorization: Bearer <gateway-admin-token>

{
  "bundle": { ...signed bundle... }
}
```

The Gateway stores an immutable history keyed by signed bundle SHA and a separate current-selection pointer. It intentionally does **not** enforce policy revision ordering or claim cryptographic verification.

That distinction is important for recovery. If a compromised Gateway administrator selects a structurally valid but cryptographically invalid bundle claiming a huge revision, a restored administrator can select a legitimate signed bundle afterward. The endpoint—not the transport—decides whether the signed revision is acceptable.

Enrolled device tokens can read only their own organization's current bundle:

```text
GET /v1/device-declared-policy
Authorization: Bearer <device-token>
```

Ordinary integration tokens cannot use this device policy endpoint. Device tokens cannot publish bundles.

## Configure a managed endpoint

Copy the raw `public_key` value from the public key JSON document into endpoint configuration:

```json
{
  "gateway": {
    "enabled": true,
    "url": "https://gateway.example.com",
    "verify_tls": true,
    "declared_policy": {
      "enabled": true,
      "organization_id": "acme",
      "refresh_seconds": 60,
      "trusted_keys": {
        "acme-policy-2026": "REPLACE_WITH_BASE64URL_PUBLIC_KEY"
      }
    }
  }
}
```

`declared_policy.enabled` defaults to `false`. When disabled, no managed-policy request is made.

By default, an enabled managed policy is atomically written to the same endpoint data directory as `declared_policies.json`. `WORKFLOW_OBSERVER_POLICY_FILE` remains respected, and an explicit `declared_policy.target_file` may be configured when a deployment intentionally uses another path.

## Endpoint acceptance rules

Before changing the active manifest, the endpoint requires all of the following:

- the response contains a signed bundle;
- bundle `organization_id` exactly matches the configured organization;
- `key_id` exists in the endpoint's pinned `trusted_keys`;
- the Ed25519 signature verifies;
- the manifest SHA matches the signed content;
- the manifest passes the normal declared-policy structural validator;
- the incoming revision is not lower than the last locally verified revision;
- the same revision never changes signed bundle bytes.

Only after those checks does OpenWorkGraph atomically replace the managed declared-policy file and persist the verified revision/hash state.

If the active managed file is locally modified later, the next refresh of the same verified bundle repairs that drift. This is an explicit consequence of enabling organization-managed policy.

## Failure behavior

Managed-policy failures are deliberately isolated from ordinary capture and evidence synchronization:

- an invalid signature does not replace the last verified policy;
- a rollback does not replace the last verified policy;
- a malformed/invalid manifest does not replace the last verified policy;
- a managed-policy error is recorded in endpoint sync state;
- evidence upload continues under the existing independent sharing/privacy policy;
- local capture remains independent of the Gateway.

If the Gateway has no signed bundle published, the endpoint retains its current verified policy.

## Rollback protection boundary

Current rollback protection is **endpoint-local state**, not TPM/secure-enclave/MDM monotonic storage. It protects against ordinary server/network rollback as long as the endpoint's local state is retained.

A machine administrator who can rewrite the endpoint's files/state can defeat this software-only rollback memory or disable the connector entirely. Hardware/MDM-backed monotonic policy state is a separate future hardening layer.

## Key rotation

Rotation is explicit and does not require sharing private keys with the Gateway:

1. generate a new offline keypair;
2. distribute/pin the new public key on endpoints while the old key remains trusted;
3. sign a higher revision using the new `key_id`;
4. confirm fleet acceptance;
5. remove the old public key from managed endpoint configuration when the overlap window ends.

An endpoint accepts only keys that are explicitly pinned locally.

## Security properties and non-properties

This layer provides:

- policy authenticity and integrity at the endpoint;
- organization binding;
- separation of signing authority from Gateway administration;
- key rotation through explicit `key_id` pinning;
- software-local rollback/equivocation checks;
- immutable Gateway bundle history plus recoverable current selection;
- no requirement for an OpenWorkGraph cloud account or hosted control plane.

It does **not** provide:

- policy confidentiality by itself — use HTTPS and customer-controlled infrastructure;
- protection against a malicious local machine administrator;
- hardware-backed rollback memory;
- HSM-backed private-key custody;
- automatic interpretation of natural-language SOP documents;
- automatic enforcement/blocking of human or agent actions.

The signed declared-policy layer remains governance context. Enforcement remains a separate future decision surface.
