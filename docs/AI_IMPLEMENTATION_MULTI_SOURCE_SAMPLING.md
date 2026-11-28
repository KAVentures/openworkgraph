# Collect a multi-window work sample for AI implementation

Use the existing observed-work selector to choose one to twelve explicit
executions per bounded time window. Select **Append selected executions to
sources** to add the exact retrieved original file to the current ordered
evidence list instead of replacing it. This supports assembling samples
across days, workflow variants or separately authorized evidence exports.
There is no automatic capture, sampling, department classification, source
authorization change or external transmission.

Each append verifies the returned selection and window using the existing
selector, then checks the combined local file count (at most eight), total
original file bytes (at most 8 MiB), canonical structure and byte-identical
duplicates before changing the source list. A refused append leaves the
current list and prepared packet unchanged. Successful append or removal
invalidates the prepared packet and individual proposal review. Existing
move-earlier/move-later controls preserve explicit source ordering; use
the per-file Remove button to exclude a file before preparing again.

The local source inventory shows bundle-scoped execution and event
occurrences, file bytes, source-limit flags, truncated and empty excerpts,
and unverified membership. Pairwise repeated execution IDs and repeated
run/event references are flagged as possible overlap, not automatically
deduplicated or counted as unique work. Identical IDs from independently
exported stores are not proof that they describe the same work. The
inventory is descriptive only; it does not verify identities, source
authenticity, representativeness, workflow membership, or actual workload.
File order determines the existing scoped references. Keep the original
files and the same order if resuming a proposal review.

The existing authenticated packet builder remains authoritative for
canonical validation, semantic duplicate source detection, event limits and
the selected context byte budget. The browser inventory is not a substitute
for server validation. If the combined packet is too large, narrow the
selected sample or choose the appropriate existing budget; nothing is
silently truncated by this feature. Inspect overlap and coverage before
sharing evidence with an authorized AI. Existing replace, import,
checkpoint, separate and single-file authoring downloads remain available.

Validation: run `node --test tests/js/ai_implementation_source_inventory.test.mjs`.
The integrated browser regression additionally exercises the existing
selector and real packet-preparation route with fixture evidence reads,
duplicate refusal preserving the old packet, append, overlap display,
removal and renewed preparation. The integrated browser regression still
requires combined CI. Isolated browser smoke uses synthetic file objects
and does not establish real department onboarding or representative sampling.
