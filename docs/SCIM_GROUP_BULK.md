# Mixed Users/Groups bulk provisioning

An enabled provisioning provider can create Users and place them in a direct Group in one request to its existing `/scim/v2/{provider_id}/Bulk` endpoint. Groups remain provider-owned metadata: they do not create teams, scopes, SSO identity bindings, consent or evidence permissions.

The existing limits remain **50 operations and 64 KiB**. The whole envelope/path structure is checked before writes. Each operation then rechecks current provider authority and uses the existing User or Group lifecycle transaction. Each operation commits separately; `failOnErrors` stops subsequent work without undoing earlier commits. Revocation or disable stops immediately at the first unauthorized operation. Inspect every response item.

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:BulkRequest"],
  "Operations": [
    {
      "method": "POST", "path": "/Users", "bulkId": "worker",
      "data": {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "worker@example.test", "active": true
      }
    },
    {
      "method": "POST", "path": "/Groups", "bulkId": "team-metadata",
      "data": {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": "Analysts",
        "members": [{"value": "bulkId:worker", "type": "User"}]
      }
    }
  ]
}
```

POST supports `/Users` and `/Groups`. PUT/PATCH/DELETE use exact resource IDs or a `bulkId:` identifying a successful earlier POST of **that same resource type**. IDs must be unique across all POSTs in one request. The map is discarded afterwards. Forward, failed, unresolved, previous-request and cross-type references fail; the server never adopts an existing identity by email or external ID. A lost create acknowledgement can leave the resource committed without establishing a usable reference. Read current state and reconcile before retrying.

Group member references resolve only in `members[].value` for POST/PUT, the values of supported PATCH add/replace `members` operations, and the quoted value of the existing PATCH removal selector `members[value eq "bulkId:worker"]`. PATCH add also supports one member object. Existing limits of 200 distinct direct active Users and 20 PATCH operations remain. Members must still belong to this provider and organization and have the current offboarding generation. A User offboarded or deleted earlier in the batch cannot become an effective member.

All other values stay literal. `displayName` and `externalId` containing `bulkId:` text are not rewritten. Unknown member attributes, `$ref` URLs, nested Group members, generic selectors, encoded/traversal resource paths and unsupported resource types are refused. There is no reference fetch or recursive string substitution. Input request objects are not modified.

Updates/deletes still require the **explicit current ETag** in the operation's `version` field. A resolved resource ID never supplies or refreshes a version. Membership changes can also change a User's read-only `groups` projection and ETag, so an earlier response version may no longer be current after later operations. Read-only id/meta echoes are ignored on Group PUT; endpoint identity and the version check remain authoritative. Each Group PATCH is atomic even though the batch is not.

The response contains method, status, location, version where applicable, POST bulkId and minimized error packets. It omits resource bodies; a failed POST has no location. Existing independent consent, offboarding, provider revocation and role/evidence boundaries remain unchanged. No batch or credential is retained in browser storage.

This is a bounded implementation of the public [RFC7644 bulk patterns](https://www.rfc-editor.org/rfc/rfc7644.html#section-3.7), not complete RFC/IdP conformance. Nested/forward/circular references, generic URI references, durable batch jobs, permission mappings and customer-controlled IdP/company acceptance remain separate under #412/#370. Public RFC text reviewed 2026-10-29, SHA256 `b9078e016c98b86934a23c80926b74fc8b59885ab2943729ec2493c17488a09b`.
