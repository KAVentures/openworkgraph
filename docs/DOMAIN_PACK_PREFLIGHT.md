# Domain-pack preflight and read-only mapping trial (R23)

Open **Set up a domain pack** for a retained log. Choose exact roles and
parameters, then **Preview and test mapping**. This resolves the proposed
configuration without installing the pack, replacing existing metrics,
or changing the retained event log.

The preview shows counts of recorded events or objects for each mapped
role, resolved definitions, omitted optional roles, exact definition
additions/removals relative to the installed configuration when available,
and the projected saved-metric library size. It identifies collisions
with metrics the proposed pack does not own, library capacity, and
downgrade blockers. Definition differences compare stored structure, not
semantic equivalence.

For logs of at most 250,000 events and 250,000 objects, the trial calls
the existing case metric, compliance, object-rule, and reference-model
engines directly on the resolved configuration. It preserves their
existing work bounds and unknown-result semantics. Larger populations
receive setup checks only, with the unavailable trial explicitly marked.
No sampled trial is presented as full coverage. The workspace lock
keeps the trial's retained log revision consistent during the analysis.

Review the results before selecting Install. Once a preview is requested
in the setup panel, changing inputs or a failed preview/save requires a
new preview in that panel. The saved configuration and current report
remain unchanged until the explicit install succeeds.

## Optimistic installation pin

The preview token covers the exact log ID and append revision, proposed
pack ID/version, mapped roles, effective parameters, resolved definition
digest, prior installation token, and retained metric library IDs and
definitions. A preview-backed install sends that token as
`expected_preview` alongside the existing installation token.
The install transaction recomputes it before replacing any owned metrics.
If data, configuration or the metric library changes, the transaction
refuses the save with a conflict. Existing clients may continue installing
without a preview token; the ordinary validation and ownership checks
remain mandatory. A preview token is an equality check, not a signed
approval, policy attestation or execution authorization.

## API and evidence

POST /v1/event-logs/{log_id}/domain-packs/{pack_id}/preflight accepts the
same configuration as the install endpoint. Local dashboard session and
strict JSON required; no-store response. The response contains the proposed
definitions, setup checks, trial results where available, and preview token.
Send the token only with the exact configuration to the existing install
endpoint. Exporting the preview creates a local JSON copy, which may contain
case/object examples and mapped labels; share only where authorized.

No provider calls, source-system verification, causal claims, actual
business compliance or independent business outcomes are asserted. The
tests cover trial/report agreement on the existing order-to-cash fixture,
no mutation, collisions, parameter changes, append invalidation and
authenticated HTTP behavior. Real source-system validation and large
population performance remain separate acceptance work.
