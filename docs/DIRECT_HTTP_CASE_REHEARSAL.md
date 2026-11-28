# Offline direct HTTP case-suite rehearsal (R17)

After reviewing an OpenAPI proposal (or manually reviewing the direct HTTP
operation catalog), and mapping activities to catalog-bound tool schemas,
rehearse a customer-authorized shadow case suite before any execution intent
is prepared or granted.

From a generated customer runtime project:

```sh
python rehearse_http.py --cases reviewed-cases.json \
  --bindings reviewed-bindings.json \
  --operations reviewed-operations.json --operations-sha256 EXACT_CATALOG_PIN \
  --output http-rehearsal.json --confirm-local-case-review
```

The default specification is the project's workflow.spec.json. Supply the
same exact spec, reviewed case population, bindings and catalog used for
the intended implementation. The operation catalog must be reviewed and
match its independently supplied digest. The case suite uses the existing
reviewed shadow-case schema and is limited to 50 cases with up to eight
accepted routes each.

The rehearsal validates each accepted route with the existing executor's
preparation function and the exact direct HTTP adapter's wire-construction
checks: origin, method and read/write classification, catalog-bound schema
pins, exact argument set/types, path safety, query/body construction,
expected-version and idempotency headers, request byte limits and reviewed
GET preconditions. It never creates a transport or calls a provider.

Every route is accounted for. A preparation rejection, unmet preparation
requirement, or wire-contract rejection blocks that route; a case matches
only when all its accepted routes match. Clarification-only cases remain
unrehearsed rather than counting as success. The result retains only opaque
case identifiers, categories, route indexes, fixed reason codes, counts
and exact input/output digests. It does not expose raw arguments, URLs,
headers, bodies, exception messages or source record values. The output
may still reveal the size and category of the suite; handle it as local
customer evidence. Keep the original inputs securely for reproduction.

The CLI writes a new file only. Exit 0 means all selected cases' reviewed
routes passed these offline checks; exit 1 means at least one route was
blocked, exit 3 means at least one case was clarification-only with no
blocked route, and exit 2 means the input or output was refused.

The result does not establish that the cases are representative, the
provider accepts the request, the provider has current availability, a
live precondition holds, a result predicate is met, human work is complete,
a source read is authorized, or a business outcome occurred. Deferred
human activities, manual conditions, source rules, decision tables and
terminal source checks are counted, not accepted. No execution intent or
credential is read; the existing real execution path still validates the
separate intent before doing the same wire checks.

The generated runtime acceptance suite exercises the actual adapter and
executor preparation with safe synthetic input, invalid wire arguments,
stale pins, disabled mappings, clarification cases and no transport.
Repository core tests independently check all 256 combinations of four
route results using controlled callbacks. These test different layers;
neither is a live-provider acceptance test.

This addition is stacked on #1632 (and its #1618 prerequisite). Keep the
parent heads unchanged while their checks run; integrate and validate the
combined current-main tree before merging this child.
