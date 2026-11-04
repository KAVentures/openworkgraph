# Local semantic-tag review proposals (Worker C, #326/#264/#370)

This optional **offline** candidate scorer takes existing reconstruction observations and a separate operator-curated mapping of event IDs to **opaque, non-sensitive controlled-vocabulary category identifiers**. It reads the production risk-prioritized candidate queue, requires two common tags by default, and emits digest-bound, ranked, non-authoritative human-review proposals. It never changes canonical membership, grants, provider data or source events. It does not send data to an LLM, cloud service or external provider.

Run from the repository root:

```sh
python -m server.reconstruction_tag_scoring --events local-events.json --tags local-tags.json --consent-local-tags
```

Example tags file (event IDs must correspond to the source events):

```json
{"event-1":["invoice","approval"],"event-2":["invoice","approval"]}
```

The source queue must be complete (500 candidates maximum); overflow refuses rather than ignoring unseen competition. Unsafe source conflict, taint and missing/cross-scope flags veto candidates. Equal or near-equal competing proposals abstain; selected proposals cannot reuse endpoints. The output contains numeric overlap scores and evidence digest, never the supplied tags. **Do not supply names, medical information, message contents, secrets or free text as tags.** The CLI consent flag is an explicit local operator declaration, not a substitute for the underlying capture permissions and privacy controls. Only use legitimately consented event inputs; do not export private text to generate tags.

This is a controlled-vocabulary overlap heuristic, **not autonomous semantic understanding**, calibrated confidence, verified case assignment or user-confirmed workflow. Tags must be curated outside this module; there is no automatic tag extraction. Independent family-held-out assignment F1 >= 0.90, contamination <= 0.05, interruption rejection and a real consenting-user pilot are still required before roadmap acceptance. CI tests validate algorithmic safety and wiring, not field effectiveness.
