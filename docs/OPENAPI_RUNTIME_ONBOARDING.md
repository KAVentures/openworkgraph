# Offline OpenAPI onboarding for direct HTTP runtime

R17 / #370 and #326. Follow-up to the runnable direct HTTP transport
in #1618. Generated runtime projects now include import_openapi.py.
This removes some manual API mapping work for a deliberately narrow
subset of customer API contracts. It is not a general OpenAPI client,
a live API inspection, or permission to execute a business action.

## 1. Inventory a customer-supplied local description

Provide an authorized OpenAPI 3.0.x or 3.1.x UTF-8 JSON file, not a URL.
The original file remains local. The importer does not fetch servers,
external references, examples, documentation, or credentials.

```sh
python import_openapi.py inspect --openapi api.json \
  --origin https://customer.example --out inventory.json
```

The inventory identifies candidate and unsupported operations, fixed
diagnostic reason codes, and proposed mappings without copying descriptions,
examples or default values. Unsafe path strings are omitted and counted.
The inventory includes selected API metadata, so review before disclosure.

## 2. Select operations and build a disabled proposal

For example, selection.json:

```json
{"operations":[{"path":"/cases/{case_id}","method":"get"},{"path":"/cases/{case_id}","method":"patch"}]}
```

```sh
python import_openapi.py propose --openapi api.json \
  --origin https://customer.example --selection selection.json \
  --out proposal.json
```

The proposal binds the complete canonical description digest, exact
workflow specification and selected path/method pairs. Order-independent
selection produces the same digest. The generated catalog is disabled:
reviewed=false, allow_writes=false, and each operation has
reviewed_provider_contract=false. Keep the printed proposal SHA-256
independently. Review the original description and actual provider
semantics, not only the derived fields.

Path arguments keep their original names, query arguments use query_,
body arguments use body_, and the two write headers use
owg_expected_version and owg_idempotency_key. Names must remain unique
and bounded. Explicit scalar mappings are one-to-one.

## 3. Approve the exact catalog separately from execution

After independent provider contract review, explicitly enumerate every
selected operation. For a mixed read/write example:

```sh
python import_openapi.py approve --openapi api.json \
  --proposal proposal.json --proposal-sha256 "$PROPOSAL_PIN" \
  --review-operation read_case --review-operation update_case \
  --confirm-provider-contracts --confirm-write-contracts \
  --out operations.json
```

Omit --confirm-write-contracts for read-only proposals. Approval
recomputes the entire proposal and checks both pins and exact selected
operation names. The output is validated by the actual direct adapter
and the command prints the canonical operation catalog digest. This
is a customer attestation of the reviewed contract, not independent
verification that the provider honors conditional writes or idempotency.

Use execute_http.py --operations operations.json --operations-sha256 PIN
--catalog catalog.json, then complete the existing reviewed binding,
precondition, independent source/outcome and intent workflow described
in DIRECT_HTTP_RUNTIME.md. Nothing in import_openapi.py invokes a
business tool or enables an intent. The direct runtime still enforces
the complete catalog-bound schema pin and exact case authorization.

## 4. Review API upgrades

```sh
python import_openapi.py compare --openapi revised-api.json \
  --previous-operations operations.json \
  --previous-operations-sha256 "$CATALOG_PIN" --out comparison.json
```

The comparison validates the prior catalog against the same workflow,
matches exact method and path, and identifies changed mappings,
arguments, types, renamed or unavailable operations. It never silently
substitutes an endpoint or carries forward approval. When every prior
operation is representable, new_disabled_proposal can be saved as a
separate proposal file for fresh review. Otherwise inspect the new
inventory and explicitly select a new scope. Even identical representable
contracts require new review; response semantics and provider behavior
may have changed without changing the request mapping. Existing old
catalogs are not remotely revoked by this offline utility.

## Supported subset and refusals

The subset intentionally follows the direct adapter rather than
pretending to support every OpenAPI feature:

- Exact normalized HTTPS origin (numeric loopback HTTP for explicit
  local development), no base path, no server variables; an operation
  or path server declaration overrides the root declaration.
- A single declared HTTP bearer security requirement with no alternative
  or combined scheme; no OAuth acquisition, cookies, custom headers
  or embedded credentials.
- GET, POST, PUT, PATCH; unique safe operationId, safe literal paths or
  whole-segment placeholders, required scalar path/query parameters.
  Operation-level parameter overrides are supported when both
  declarations fit the subset. Ambiguous overlapping paths are refused.
- Writes explicitly declare required string If-Match and Idempotency-Key
  headers, and a required application/json body with only required
  scalar properties and additionalProperties=false.
- Explicit 200/201 application/json object success response. The
  response schema's fields and constraints are not turned into result
  checks; independently review those and actual response semantics.
- Bounded local components references only, maximum eight reference
  steps, no external references, reference siblings, custom dialects,
  callbacks, composition, nested request objects, optional arguments,
  defaults, enums, patterns, nullable or other unsupported request
  constraints. No automatic mapping of unknown semantics.

Maximum 2 MiB input/output, 300 path items, 40 selected operations,
30 arguments per operation. Some valid OpenAPI files are intentionally
unsupported, including APIs with common additional schema constraints,
server base paths or complex authentication. Manual reviewed adapters
are still required. The OpenAPI specification's inheritance,
serialization and security model is documented at
https://spec.openapis.org/oas/v3.1.2.html; this tool is a restricted
implementation, not a conformance claim.

## Validation

The exact committed compiler and new test files were reproduced locally,
and their Git blob hashes matched:
- compiler: 45e97ba1c2d0f6ae89d47a9abfd114a4a75e13ee
- tests: 57e06b202e105935333b217059dd18186e4c59cd

All 11 isolated unittest tests passed, including 250 generated mapping
cases and unsupported-constraint rejections. The tests used copied
execute helper functions and the exact 9,840-byte prefix of the actual
direct adapter (through request construction, prefix Git-blob SHA-1
41d78ce96c6f34cf7ef975bef95b8e8fe16d653a). Execution-only functions
were replaced by fail-fast stubs in this isolated test environment.
No HTTP request, provider or full executor was exercised locally.

The generated archive and manifest acceptance inventory now include
the new compiler and tests, with a focused draft CI workflow that
stages the real portable dependencies and runs generated archive
regressions. Full checkout failed GitHub DNS resolution in the authoring
environment. Integrated CI and live provider acceptance remain pending.
Land after #1618 and required tests pass.
