# OWNER redaction

OpenWorkGraph keeps workflow capture and inference unchanged, then masks identifiers only when data is presented through the dashboard/API/MCP/export surfaces.

The person running the local observer is represented as `OWNER`. OpenWorkGraph automatically tries the local OS account display name and username. Optional explicit aliases can be added to `config.json` with `owner_aliases`, plus `owner_emails` and `owner_phones` when desired.

Other high-confidence people are represented by stable `PERSON_xxxxxx` tokens. Gmail/Outlook composite accessibility labels such as `Select Anna Svensson, Contract renewal` are transformed to `Select PERSON_xxxxxx, Contract renewal`, preserving the subject and action.

High-confidence person aliases learned from structured email/UI evidence can be recognized in later displayed strings. The local registry stores only keyed hashes of aliases and their pseudonym tokens, not the literal names.

This layer must never be imported by collectors, normalizers, contextualizers, database code or workflow analytics. Raw local evidence remains available to the existing inference pipeline; presentation masking must not change event IDs, counts, timestamps, durations, actions or task boundaries.
