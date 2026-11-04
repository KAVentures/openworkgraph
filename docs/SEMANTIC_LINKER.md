# Optional AI help with grouping work

OWG links observations into work items by exact records, tabs, copy-paste
transfers and (opt-in) shared label words. Items related only by meaning, such
as a renewal email, the renewal account and the price sheet, need judgement.
The semantic linker asks a model you choose for that judgement.

Turn it on in **Privacy → AI help with grouping work**, or with
`PUT /v1/semantic-linker`:
`{enabled: true, acknowledge: "semantic-linker-v1", base_url, model, api_key_env}`.

- `base_url` is any OpenAI-compatible endpoint: https, or http only on this
  computer (for example Ollama at `http://127.0.0.1:11434/v1`).
- `api_key_env` is the **name** of an environment variable that holds the key.
  The key itself is never stored by OWG.

Then request suggestions with discovery: `GET /v1/workflow-evidence/candidates?semantic_links=true`
(also `label_topics=true` to see the label-topic items they build on). The
response gains `semantic_link_suggestions`.

What the model receives: numbered items, each with its app names and short screen
labels in time order. It receives no URLs, record IDs, clipboard contents, people
or raw metadata. Sensitive identifiers are already tokenized at storage.

What OWG does with the answer:

- validates it strictly: known item numbers only, and each item in at most one
  group;
- refuses any group that would join two different records of the same kind;
- returns the rest as `semantic_suggestion` groups with `requires_review: true`.

Suggestions are never written into canonical evidence or merged into OWG's own
links. If the endpoint is unavailable, discovery still works and reports why.

Measurement: see "Optional semantic linker" in `evals/reconstruction/README.md`.
