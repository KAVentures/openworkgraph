# Reuse reviewed process knowledge alongside fresh work evidence

After preparing the original observed-work evidence packet, expand
**Reuse saved reviewed process knowledge** and explicitly load the existing
local process catalog. Browse bounded pages and select up to five current
reviewed process designs. No automatic matching, identity inference or
selection takes place. The existing catalog remains the only source of
these saved revisions.

Prepare and download the separate knowledge companion JSON. The local
authenticated endpoint rebuilds the original evidence packet from its
original sources and byte budget, verifies its digest, and retrieves the
exact selected latest catalog revisions under the existing local owner
boundary. It recomputes each existing knowledge record content digest
using the store's original convention, retains the complete reviewed
records without rewriting their rules or unresolved questions, and
rechecks revision references before returning a bounded canonical
512 KiB companion. The companion may contain sensitive business data.
Inspect it locally and share it only with an authorized AI, along with
the separate evidence packet and original authoring instructions.

The companion is prior user-reviewed design, not observed evidence,
verified current policy, permission, or proof of applicability. The
instructions ask the external AI to compare it with new observations,
identify divergence and missing confirmation, and cite only exact scoped
events from the original evidence packet. Old knowledge references are
not event citations. The ordinary v1 proposal format and structural
review are unchanged; their event membership checks do not certify
semantic use of the companion. Original evidence and companion context
have separate digests. Nothing is written to the catalog, model, or
portfolio. No automatic external sharing or AI request occurs.

A changed evidence packet or selection clears the downloadable companion.
Catalog revision changes detected during preparation are refused. Catalog
records are read and rechecked separately, not atomically, and an export
cannot prove later revocation status. Reload the catalog before preparing
a new companion.

Validation: run `python -m pytest -q tests/test_ai_implementation_knowledge_context.py tests/test_ai_implementation_knowledge_routes.py`.
Core tests use synthetic knowledge records with the same digest convention;
isolated Chromium uses the real builder with substituted data/transport.
The integrated API regression uses the original packet producer and
substituted catalog/auth boundaries. Combined CI and real persisted-catalog,
dashboard and external-model acceptance remain necessary.


## Single-file external AI handoff

The selector also supports existing reviewed plain procedures that have no
process graph. The catalog page labels the type; exact current owner-scoped
record and digest checks are unchanged.

After preparing a knowledge companion, choose **Download original evidence
with reviewed knowledge** or **Download compact evidence with reviewed
knowledge** (when available). These actions reuse the original evidence
export validator and preserve the exact JSON text of both sections, their
separate pins and byte lengths, and the matching evidence instructions.
No extra record is selected automatically. The returned proposal must
still cite only events in the original evidence packet and use its original
packet pin. Neither section is policy or authority to execute.

Selection and packet changes invalidate the combined downloads. They remain
local text artifacts; inspect them before sharing. There is no model
invocation or new external transmission. The combined file is bounded to
9 MiB; use a narrower selection if needed. The browser checks matching
metadata and preserves exact server-returned text; it does not independently
recompute the server's canonical digests.

Validation: 11 focused Python tests and four Node tests passed; isolated
Chromium exercised the real builder and both dashboard helper modules,
with substituted transport, selecting, preparing, exporting and invalidating.
The added persisted owner-scoped route regression is unexecuted locally;
full application CI remains required.
