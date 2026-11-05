# Conflict-safe semantic suggestion groups

The optional semantic linker remains disabled until the user acknowledges the selected model endpoint. It sends only the existing minimized item labels and app surfaces and returns **review-only** suggestions; no canonical work item, case, or evidence is rewritten.

## Why

A model may suggest an email, account A and account B as one group. Since A and B are distinct records of the same provider/kind, accepting that union contaminates cases. Previously OWG discarded the entire group, including the otherwise plausible email–A link. Consensus can also create a transitive bridge: A–B and B–C may be agreed without A–C agreement.

## Behavior

The linker now builds deterministic, disjoint pair-supported compatible subgroups. Every pair inside a subgroup must have been proposed (and, in consensus mode, agreed in both orderings). A subgroup may not contain distinct resource references for the same provider and resource kind. The lowest numbered item wins an ambiguous conflict; remaining items can form a separate compatible subgroup. A singleton is left unlinked and counted in the existing `vetoed` diagnostic. This diagnostic counts unassigned model-proposed items, not necessarily distinct business cases; do not interpret it as an accuracy metric.

The algorithm is conservative and order-dependent, not a proof of common business identity. Users must review every suggestion. The local 7B holdout has not met the preregistered F1 ≥ 0.90 and contamination ≤ 0.05 gates. Do not change the default or claim a real-user accuracy improvement without independent evaluation. Consent, tenant boundaries, revocation and evidence access remain enforced by the calling discovery path.
