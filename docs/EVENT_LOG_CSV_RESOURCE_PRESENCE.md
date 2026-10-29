# CSV resource presence in saved event logs (#326 R6)

A CSV event's optional mapped resource is an **identifier**, not a measure of an employee's performance. The bulk DuckDB import now follows the shared streaming CSV reader's resource-presence rule: an empty or ASCII-space-only resource is stored as the empty string, meaning *resource not supplied*. A nonblank resource retains its exact supplied text, including leading/trailing spaces; no identity matching, case folding or employee inference is performed.

This normalization happens **only at the final bulk INSERT**, after the original value has passed the existing identifier-length and control-character validation. It does not permit tabs, newlines or other forbidden controls, and it does not change the raw uploaded bytes or the `source_sha256` digest. A newly imported log's stored-content digests reflect the normalized resource field. Existing stored logs and their historical quality reports are not rewritten.

The existing counts-only `events_without_resource` diagnostic now correctly counts whitespace-only resources imported through this CSV path as missing. The field name, REST/MCP/dashboard contracts, permissions, local-only consent, tenant isolation and deletion behavior remain unchanged. These counts describe only supplied rows, not source-system completeness or a verified staffing assignment.

A synthetic regression compares the bulk import with the shared streaming CSV reader, checks exact preservation of padded nonblank resource labels and verifies that a quoted tab-containing resource remains rejected. This does not establish real-provider or customer acceptance.
