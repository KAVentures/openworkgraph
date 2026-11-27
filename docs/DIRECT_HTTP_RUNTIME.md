# Direct customer HTTP runtime (R17)

Refs #370 and #326. Reviewed process implementation previously required a customer MCP server for actual mapped business operations. Generated customer runtime projects now include a direct REST execution target. This is a runnable transport for customer-owned operations, not another non-executing starter.

## Implementation journey

1. Export an exact reviewed process runtime project and verify its release manifest. Install requirements in an isolated Python environment.
2. Copy http-operations.example.json to operations.json. Set the exact reviewed runtime origin and operations. Keep reviewed=false until the customer has independently checked the provider's actual API and authorization semantics. The example contains no enabled operations.
3. Each operation has name, method (GET, POST, PUT, PATCH), path, fields (flat scalar types), query and body mappings (argument name to wire field), expected_version_argument, idempotency_argument, and reviewed_provider_contract. Paths use fixed safe segments or an entire segment in braces. Every input must be mapped exactly once. GET has no body or write headers; set both write argument references to null.
4. Conditional writes require allow_writes=true, a strong If-Match entity tag (or reviewed numeric version represented as a quoted entity tag), and an Idempotency-Key argument. Review that the provider actually enforces both semantics. The client cannot force a provider to honor them. APIs that require unsupported authentication, arbitrary payloads, different concurrency semantics, DELETE, or non-JSON responses require a separate reviewed adapter.
5. Calculate the canonical execute.digest of the reviewed operations file and independently record it. Run python execute_http.py --operations operations.json --operations-sha256 PIN --catalog catalog.json. This writes a new local metadata file without contacting the provider. It is compatible with the existing configure.py tool-binding proposal/rehearsal workflow; it is not an independent provider introspection result.
6. In reviewed bindings, use the exact origin as tool_server_url and the catalog input_schema_sha256 for every action and fresh read precondition. The input schema includes the full operation-catalog digest, so changing any operation, origin, argument mapping or write approval invalidates every existing schema pin. Review operation_kind against method and pin the same version/idempotency arguments in the existing write contract.
7. Prepare the exact plan, case and disabled intent template using the normal execute.py review path. An authorized customer operator separately reviews and authorizes the intent, human resolutions, source-read grant and expiry. Set OWG_EXECUTION_HTTP_TOKEN (not the MCP credential) and the existing separate source/catalog read credentials where required.
8. Execute:
   
       python execute_http.py --operations operations.json --operations-sha256 PIN --execute-approved --plan proposal.json --case case.json --bindings bindings.json --intent customer-intent.json

## Actual execution semantics

The direct adapter reuses the canonical execute_approved function. All action and precondition argument contracts are validated before execution. Exact model/route/case/binding pins, intent expiry, fresh preconditions, current business-rule source checks, catalog drift checks, durable exclusive intent journal, emergency stop, independent terminal source checks and no-replay rules remain in force. A separate local catalog digest is required in addition to the binding schema pins. There is no model invocation in the direct executor.

Each reviewed operation makes a bounded request to the fixed origin. It sends a separate bearer credential, disables ambient proxy configuration and redirects, uses a fresh cookie-free HTTP client, caps response size and accepts only structured JSON objects. The adapter does not automatically retry, follow redirects or treat an HTTP status as independent business success. An uncertain write must be investigated in the existing journal before any new authorization. The direct HTTP target does not cancel already transmitted requests or replace service-side IAM or conditional-write enforcement.

HTTPS is required except explicitly reviewed numeric loopback development endpoints. The operation catalog is trusted customer configuration, not an unrestricted URL service. Do not expose this local command to untrusted callers or share the catalog or case values unnecessarily. DNS and network policy remain deployment responsibilities. The provider may receive the reviewed case fields, and source checks remain separate. Existing MCP execution behavior and credentials are unchanged.

## Acceptance

Generated tests include the actual canonical executor with synthetic HTTP transport, a real loopback HTTP server performing GET–conditional PATCH–GET, source-receipt checks, mismatched schemas/methods, unsafe path arguments, stale preconditions, ambiguous results, redirects, timeouts, response bounds and journal replay refusal. The portable manifest and required acceptance suites include the new target. These fixtures are synthetic, not a live customer deployment or independent provider certification. CI and provider-specific acceptance are required before production use.

## Illustrative read operation (not an approved provider contract)

```json
{
  "name": "read_case",
  "method": "GET",
  "path": "/api/cases/{case_id}",
  "fields": {"case_id": "string"},
  "query": {},
  "body": {},
  "expected_version_argument": null,
  "idempotency_argument": null,
  "reviewed_provider_contract": false
}
```

A reviewed write can map its business fields into the JSON body while mapping the exact separately reviewed version and idempotency arguments into the two fixed headers. The runtime refuses unsupported or unmapped fields. Do not copy illustrative endpoint names into a live deployment without checking the actual provider contract.
