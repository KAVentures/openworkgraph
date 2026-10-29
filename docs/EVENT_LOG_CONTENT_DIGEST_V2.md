# Event-log content digest v2 — additive integrity diagnostic (#326 / #370 R6)

The quality report now exposes **content_sha256_v2** alongside the existing
**content_sha256** for each newly imported or appended batch and for the
current whole log. Both are deterministic, order-independent digests of the
stored event rows and event-object links, not of the original upload bytes.
The separate source SHA-256 and transform SHA-256 remain unchanged.

## Why a second digest?

The original content digest uses a delimiter between source-owned text fields.
A field can itself contain that delimiter, so two different tuples can produce
the same serialized row *before hashing*. NULL field omission can also make
delimiter-based serialization ambiguous. The v2 digest hashes JSON arrays of
the exact fields (including NULL and field boundaries), then uses the existing
sorted, duplicate-sensitive row-hash buckets and a v2 domain separator.
It does not rely on a new hashing algorithm or claim that the provider data
is authentic or historically complete.

## Compatibility and retention

- **v1 is unchanged.** Existing content_sha256 values and saved provenance
  references keep their meaning; no silent rehash or migration occurs.
- **v2 is additive.** Historical quality reports written before this release
  may lack content_sha256_v2. The current report computes it on demand;
  new import/append reports persist it with the same retention/deletion rules.
- Source namespace, event IDs, source-identity digests, imports, exports,
  consent, local-only access, retention, API authorization and read budgets
  are unchanged. No source reads or background work are introduced.
- This detects ambiguous field serialization, not SHA-256 cryptographic
  collisions or missing source history. Real customer/provider validation
  remains separate from owned synthetic regression fixtures.

The regression fixture demonstrates two distinct event rows that collide under
v1 delimiter serialization but have distinct v2 digests. The existing
reproducibility and append tests also cover the additive v2 field.
