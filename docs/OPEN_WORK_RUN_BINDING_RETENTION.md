# Run/case binding retention and revocation

Run/case associations are local, operator-confirmed links, not evidence of independent agent causality or customer success. They are keyed to source scope and actor and are only useful while the authenticated dashboard, source review, case revision, imported run and current consent remain admitted by their calling route.

The local binding ledger caps each association at seven days and never longer than the current human-history retention policy. When an operator shortens retention, all read paths (`get_binding`, `list_case_bindings`, and the exclusive run/case attribution guard) immediately exclude records whose creation date is older than the new limit, even if the original `expires_at` is later. A later explicitly reviewed binding can be created after stale rows are removed. Disconnection/replacement and explicit revocation remain separate immediate invalidation paths.

This does not authorize provider writes, recurring checks, cloud export, or agent execution. Provider state is not proof of a customer outcome. Regression tests cover retention shrink, actor isolation, exclusive attribution, and renewed explicit review.
