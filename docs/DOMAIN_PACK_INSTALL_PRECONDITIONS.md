# Conditional domain-pack saves

The dashboard now saves against the installation it loaded. If another editor
reconfigures, removes or recreates that pack before the save, the request returns
HTTP 409 with a request to reload setup. The rejected request changes no metrics,
installation metadata, mapping, parameters, definition digest or history. Reload
**Set up a domain pack**, review the current values, and deliberately save again.

Installed-list entries and successful install responses include an opaque
`installation_token`: a SHA-256 equality token over the complete stored
installation, including its generated owned metric IDs and history. It changes
on reinstallation even if resolved definitions stay the same. It is not an
access credential, independent source receipt or business approval.

New API callers can send `expected_installation` on the existing install POST:

- `null`: require no current installation of this pack on this log;
- a returned 64-character lowercase token: require that exact installation;
- omitted: preserve the existing unconditional behavior for older callers.

The comparison runs under the existing workspace lock before metric mutation.
Missing or malformed dashboard basis metadata cannot silently fall back to an
unconditional save. A token does not grant access: owner-session authorization
still applies and AI bearer tokens remain refused. The body remains strictly
validated and ordinary input errors still return 422.

This guards installation/reconfiguration. Uninstall remains on its existing
unconditional contract. Older callers may omit the check, and no distributed
multi-process transaction or business-policy approval is inferred. Local stale
protection supplements, rather than replaces, atomic persistence and report
snapshot invalidation in their separate changes.

Acceptance covers absent/current/stale/deleted/recreated installations,
same-definition reinstall, invalid tokens, legacy omission, concurrent first saves,
actual authenticated HTTP 409 and AI refusal, and Chromium with a second editor
saving before the first dashboard submits its stale basis. Source data and
provider accounts are not used.
