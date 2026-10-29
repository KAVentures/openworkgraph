# SCIM search and selected identity fields

Provisioning clients can use `POST /scim/v2/{provider_id}/Users/.search` or `/Groups/.search` to query without putting filter values in the URL. These are read-only operations over the existing provider-owned identities and current direct memberships. They do not provision or adopt identities, change consent, map permissions, or grant human/admin/evidence access. A disabled/revoked/wrong provider credential is refused before parsing the body; the underlying list transaction checks current authority again.

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:SearchRequest"],
  "filter": "externalId eq \"native-worker\"",
  "attributes": ["userName", "groups.value", "meta.version"],
  "startIndex": 1,
  "count": 10
}
```

Search returns the same `ListResponse`, total, ordering and pages as GET. Users support `userName eq` and `externalId eq`; Groups support `displayName eq` and `externalId eq`, each with one JSON string. User-name matching keeps the existing normalized case-insensitive email behavior; Group names/external IDs keep their exact matching. Generic expressions, sorting, root/cross-resource `/.search`, extension schemas, and search query-string options are refused. Request bodies accept SCIM JSON or JSON, at most 64 KiB, with no duplicate/case-colliding names, nonfinite values or malformed/deep JSON. Filter strings are at most 1,000 characters; decoded identity filter values containing controls or invalid Unicode are refused before reaching either database. POST pagination uses actual JSON integers, never booleans/floats/strings; GET uses decimal digits. `startIndex` is 1 through 2,147,483,647; count is 0–200 for Users and 0–100 for Groups (default 100). Count zero returns totals and no resources. Pages are current reads, not a multi-request snapshot.

GET resource/list reads also accept `attributes` or `excludedAttributes` as comma-separated paths. In a SearchRequest these are arrays of paths. Use one option per request; both together are refused. Each selection has 1–32 paths totaling at most 4,096 characters. Names are case insensitive, may have surrounding whitespace, and can use their resource's full core-schema prefix. Unknown, cross-schema, selector/expression, or unsupported paths are refused rather than returning broader identity fields.

Supported paths:

| Resource | Top-level attributes | Supported subattributes |
| --- | --- | --- |
| Both | schemas, id, externalId, displayName, meta | meta.resourceType, meta.created, meta.lastModified, meta.version, meta.location |
| User | userName, active, groups | groups.value, groups.display, groups.type |
| Group | members | members.value, members.type |

`schemas` and `id` are always returned even when excluded or not selected. Selecting a complex parent returns all its supported children. Otherwise subattribute selection/exclusion applies independently to each existing member/group without merging or reordering entries. An absent optional field is not invented. Excluding all children may leave empty array objects; that is field minimization, not proof the membership is absent. Excluding an entire complex parent removes it. List envelope fields are unaffected.

Selection happens after the existing current-state projection and location decoration. It never rewrites stored data, changes lastModified, writes an audit, or recomputes versions from fewer fields. Resource GET responses keep the original full-resource `ETag` and `Location` headers even if `meta` is omitted; conditional writes must use that full-resource version. A later membership/lifecycle change can make it stale as usual. Writes and Bulk still refuse selection options. Existing manual-identity adoption, exact version, offboarding and rehire rules are unchanged.

This implements the bounded patterns in public [RFC7644 §§3.4.2.5/3.4.3/3.9](https://www.rfc-editor.org/rfc/rfc7644.html#section-3.4.3). It does not claim full SCIM or real IdP compatibility. Customer-controlled IdP mappings, broader attribute/filter support, company rollout and recovery remain under #412/#370/#264.
