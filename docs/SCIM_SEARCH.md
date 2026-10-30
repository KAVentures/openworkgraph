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

Search returns the same `ListResponse`, total, ordering and pages as GET. Users support `userName eq` and `externalId eq`; Groups support `displayName eq` and `externalId eq`, with 1–8 equality clauses joined by case-insensitive `and` (each value is one JSON string). User-name matching keeps the existing normalized case-insensitive email behavior; Group names/external IDs keep their exact matching. OR, parentheses, nested/complex selectors, non-string comparisons, generic expressions, sorting, root/cross-resource `/.search`, extension schemas, and search query-string options are refused. Request bodies accept SCIM JSON or JSON, at most 64 KiB, with no duplicate/case-colliding names, nonfinite values or malformed/deep JSON. Filter strings are at most 1,000 characters; decoded identity filter values containing controls or invalid Unicode are refused before reaching either database. POST pagination uses actual JSON integers, never booleans/floats/strings; GET uses decimal digits. `startIndex` is 1 through 2,147,483,647; count is 0–200 for Users and 0–100 for Groups (default 100). Count zero returns totals and no resources. Pages are current reads, not a multi-request snapshot.

For example, `userName eq "person@acme.test" and externalId eq "native-person"`
requires both fields on the same provider-owned User. Repeated clauses are
allowed and count toward the eight-clause budget; contradictory clauses match
nothing. A quoted value containing `and`, escaped quotes, or SQL-looking text
stays one literal value. Every clause must parse before any query runs; a bad
later clause is never silently discarded. Count and page queries use exactly
the same conjunction and provider/tenant/deletion scope. This does not add
new matching fields, email matching or name matching, group-name uniqueness or a complete
IdP mapping contract. The Microsoft [matching-query guide](https://learn.microsoft.com/en-us/entra/identity/app-provisioning/use-scim-to-provision-users-and-groups#retrieving-resources)
is a public request-shape reference; real customer IdP acceptance remains open.

GET resource/list reads also accept `attributes` or `excludedAttributes` as comma-separated paths. In a SearchRequest these are arrays of paths. Use one option per request; both together are refused. Each selection has 1–32 paths totaling at most 4,096 characters. Names are case insensitive, may have surrounding whitespace, and can use their resource's full core-schema prefix. Unknown, cross-schema, selector/expression, or unsupported paths are refused rather than returning broader identity fields.

Supported paths:

| Resource | Top-level attributes | Supported subattributes |
| --- | --- | --- |
| Both | schemas, id, externalId, displayName, meta | meta.resourceType, meta.created, meta.lastModified, meta.version, meta.location |
| User | userName, active, groups, name, emails | groups.value, groups.display, groups.type; name.formatted, name.familyName, name.givenName, name.middleName, name.honorificPrefix, name.honorificSuffix; emails.value, emails.type, emails.display, emails.primary |
| Group | members | members.value, members.type |

`schemas` and `id` are always returned even when excluded or not selected. Selecting a complex parent returns all its supported children. Otherwise subattribute selection/exclusion applies independently to each existing member/group without merging or reordering entries. An absent optional field is not invented. Excluding all children may leave empty array objects; that is field minimization, not proof the membership is absent. Excluding an entire complex parent removes it. List envelope fields are unaffected.

Selection happens after the existing current-state projection and location decoration. It never rewrites stored data, changes lastModified, writes an audit, or recomputes versions from fewer fields. Resource GET responses keep the original full-resource `ETag` and `Location` headers even if `meta` is omitted; conditional writes must use that full-resource version. A later membership/lifecycle change can make it stale as usual. Writes and Bulk still refuse selection options. Existing manual-identity adoption, exact version, offboarding and rehire rules are unchanged.

This implements the bounded patterns in public [RFC7644 §§3.4.2.5/3.4.3/3.9](https://www.rfc-editor.org/rfc/rfc7644.html#section-3.4.3). It does not claim full SCIM or real IdP compatibility. Customer-controlled IdP mappings, broader attribute/filter support, company rollout and recovery remain under #412/#370/#264.
