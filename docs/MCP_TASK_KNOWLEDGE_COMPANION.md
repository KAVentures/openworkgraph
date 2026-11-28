# Task-specific reviewed knowledge alongside observed context

Existing local MCP `get_task_context` now accepts optional
`knowledge_refs`: up to three exact objects with `workflow_id`,
`revision`, and `content_sha256`. Discover candidates using
`get_workflow_knowledge(query=..., summary_only=true)`, inspect relevance,
and select explicit references. The default call performs no knowledge
lookup and retains its previous response shape.

The task-context tool still reads the existing observed procedure, prior
similar runs, failure and approval patterns, and separately declared
policy. Selected reviewed knowledge is then read through the existing
authenticated AI-visible exact-ID endpoint, using the existing detail
settings. Missing, stale, malformed or oversized selections are refused.
The companion is bounded to 128 KiB before the trust annotation. Each
record retains the selected reference and a fingerprint of its actual
AI-visible API response. Stored digest references are matched, not
cryptographically verified against potentially redacted content.

The companion has a separate existing reviewed-knowledge trust annotation,
not the observed-evidence annotation. The original source API fingerprint
still covers only the original task response; the final protected result
fingerprint covers the entire returned representation, including any
companion. Separate reads are not atomic. No relevance, semantic use,
policy validity, business outcome, or execution permission is certified.
Agents must reconcile conflicts and unresolved questions with the user,
and independently verify consequential outcomes. No automatic knowledge
selection, external provider call, new store or write is introduced.

Validation: run `python -m pytest -q tests/test_task_knowledge_companion.py
tests/test_task_knowledge_companion_registration.py`. The latter exercises
both existing local tool entry points with substituted transport and audit
boundaries. Full live connector and enterprise acceptance remain required.
