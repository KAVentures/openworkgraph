# Transfer domain pack setups between logs (R23)

An analyst can reuse a reviewed domain-pack configuration for a second
retained log, a different customer-controlled deployment or a repeatable
setup procedure. This is setup portability, not a transfer of an installed
dataset, historical results, source authority or business approval.

## Dashboard

1. Open a retained log with an installed domain pack. In **Set up a domain
   pack**, select the installed pack and expand **Transfer a saved setup
   between logs**.
2. Select **Download installed setup only**. This reads the saved installation,
   not unsaved form edits or the currently displayed report.
3. On the destination log, open the same pack and review the setup JSON file.
   The server checks its exact schema, version and catalog definition.
4. Examine the result for each role. An exact label found in the destination
   is a candidate with a retained row count, not a verified semantic mapping.
   A missing previously mapped label differs from an optional role that was
   intentionally unmapped in the exported setup. Imported candidates replace
   the unsaved form only, with missing roles cleared rather than guessed.
5. Explicitly review or change all mappings and parameters. Use **Preview and
   test mapping**. The UI requires this fresh preview after loading a template,
   and sends the destination preview and installation tokens with installation.
   The existing server transaction revalidates the tokens and checks metric
   ownership. The source template cannot authorize destination installation.

The file is bounded to 128 KiB. It includes a format/version, pack identifier,
catalog-definition SHA-256, exact role-to-label mappings, effective numeric
parameters and a normalized configuration SHA-256. It contains no case or
object identifiers, source log identifier, timestamps, reports, owned metric
IDs, installation or preview tokens. Mapped label names can still be sensitive;
share files only within the intended authorization boundary.

The digests establish equality to the local definition and configuration,
not authorship, signature, approval or authenticity of the source data. The
destination UI accepts the latest local version only; older versions require
explicitly reviewed new setup rather than implicit upgrade. File upload is
non-retained and never triggers writes or external calls.

## API

Dashboard-session-only endpoints:

- GET /v1/event-logs/{log_id}/domain-packs/{pack_id}/template
- POST /v1/event-logs/{log_id}/domain-packs/{pack_id}/template/review

The second returns exact destination role matches and counts, suggested
mapping, missing roles and parameters. No destination data changes. The
ordinary install API remains backward compatible; callers must separately
request the destination preflight and explicitly supply its token if they
want optimistic concurrency guarantees.

Tests cover exact retained OCEL import and transfer, source-only configuration
export, fresh destination preflight/installation, absent and omitted roles,
catalog drift, malformed or changed files, HTTP access boundaries and browser
download/review/reinstall. Synthetic tests do not establish interoperability
with real customer exports or adoption.
