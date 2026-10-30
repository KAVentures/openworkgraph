# Provider-owned SCIM contact emails

`emails` is optional descriptive contact metadata attached to the existing
provider/employee link. It never changes roster `userName`/email, the evidence
actor, `displayName`, verified SSO identity, invitation destination, consent,
credentials or permissions. Addresses are unverified; storing or selecting one
does not prove mailbox ownership or that two employees are the same person.
Different employees/providers may have the same contact. Existing manual-identity
adoption, assignment and tombstone protections still apply.

## Bounded representation

Up to sixteen objects, each with a required `value` (1–320 Unicode code points),
optional `type` (nonblank, up to 64), optional `display` (up to 512, literal blanks
allowed), and optional Boolean `primary`. A value is a bare address with exactly
one `@` and nonempty sides; whitespace, angle brackets, quotes, backslashes,
commas and semicolons are refused. This is a bounded address form, not a complete
RFC5321/5322 mailbox parser, deliverability check or domain/ownership validation.
All strings reject controls, invalid Unicode and the existing forbidden code
points. Unknown/case-colliding keys and invalid JSON/types refuse the request.

Strings retain literal Unicode, case and spacing. Missing/null type/display are
unassigned; a missing/null primary becomes false. Missing/null value is refused
on any retained entry. At most one entry may be primary; zero is allowed.
Duplicate case-folded `(value,type)` pairs are refused. The same address with
different types is allowed. Entries sort by those keys, so a reordered array or
omitted versus explicit false does not invent a version/audit change. There is
no global alias uniqueness or email-based person matching.

Contact `display` is readWrite descriptive metadata independent of roster
displayName. This follows the explicit readWrite schema field and the proposed
description correction in [RFC7643 erratum8358](https://www.rfc-editor.org/errata/eid8358),
which is **Held for Document Update**, not Verified. Discovery advertises this
bounded contract, including the required value.

## Atomic writes and selectors

Create accepts a complete optional array. PUT replaces the whole array; omission,
null or `[]` unassigns it. PUT omission of `active` still never implicitly rehires.
PATCH root/pathless `replace` also replaces the whole array. Root `add` merges
only supplied children into an existing value/type pair or adds a new entry;
unsupplied primary/display are preserved. Empty add is a no-op; explicit root
null or remove unassigns all contacts. Multiple incoming primary=true entries
are refused. Selecting one new primary demotes all others in the same atomic
operation. Every operation stages against the result of the previous one.

Supported PATCH paths (up to 1,024 characters) are `emails`, `emails.child`,
`emails[type eq "JSON string"]`, `emails[value eq "JSON string"]`, and either
selector followed by `.value`, `.type`, `.display` or `.primary`. A full core
User URN prefix is also supported. Structural names and `eq` are case insensitive;
quoted/escaped literals are decoded before case-folded value/type comparison.
Literal text is never lowercased as part of parsing. Selectors are evaluated
over at most sixteen contacts, never interpolated into SQL. Other operators,
compound filters, nesting, array indexes and broader path grammar are refused.

A filtered object add/replace merges supplied children into each matched entry.
A child update changes that child on each match. A primary=true update must
select exactly one entry; duplicate pairs or invalid resulting entries refuse
the entire request. Remove of a whole selected entry or its required value
removes that entry. Removing type/display unassigns it; removing primary makes
it false. Missing remove targets succeed without changes. Missing add/replace
targets return 400/noTarget **except** `add emails[type eq "work"].value`, which
creates a contact using the literal type and supplied address. This bounded
zero-match add interpretation is explicit; [erratum8097](https://www.rfc-editor.org/errata/eid8097)
records the Microsoft ambiguity and is Held for Document Update. Other missing
targets never synthesize partial contacts. Errors/bounds in any later operation
roll back earlier contacts, names, lifecycle/credential effects and audits.

All changed contacts participate in the existing full-resource ETag before
field minimization. Stale supplied versions are refused. No-contact resources
retain their prior projection/version. Contact-only changes preserve the roster
row and existing credentials; human roster edits still invalidate old versions.
The separately approved absent-version policy remains limited to standalone
User PATCH; supplied versions, Bulk and direct service calls remain strict.
Existing minimized audits contain no contact values or selector text.

## Reads, upgrades and retention

GET and POST search selection support `emails` and its four children, with the
same full-resource ETag. Email list filtering/matching, enterprise extensions,
other contact attributes and Group-name uniqueness are not added. Configure
matching on supported stable userName/externalId; synthetic Microsoft-shaped
requests are not real Entra/Okta tenant certification.

The additive `gateway_scim_user_emails` table uses a provider/employee composite
key and foreign key to the existing link. Current backups preserve it; an older
database without contacts gains an empty table and unchanged old resource
versions. Invalid restored metadata returns a generic 503 without the stored
value. Names and contacts share provider-first serialization with current
employee/link locks; PostgreSQL never locks a nullable outer-join side.

**Upgrade every serving worker before mapping contacts.** Old workers omit them
from versions and cannot safely enforce this contract. Mixed old/new workers
and downgrade with contacts are unsupported. Restore/anti-rollback precautions
in the provisioning guide still apply; this adds no separate revocation authority.

Contact metadata follows the existing identity-link tombstone retention, not
canonical evidence retention. Offboarding/delete do not silently erase it or
permit adoption; disabled/revoked providers cannot read or mutate it. It stays
in identity database backups until those are removed under the deployment's
identity/backup policy. No new background sync, email delivery, source access,
observation, AI disclosure or customer IdP action is enabled.
