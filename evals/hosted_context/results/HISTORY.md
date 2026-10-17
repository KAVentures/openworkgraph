# Archived hosted-context model attempts (October 2026)

## October 17 targeted retry

A fresh bounded retry used Codex CLI 0.159.2, requested model `gpt-5.4`, one
trial each of `recent-continuity` and `general-coding`, with OWG enabled and
disabled (four attempts, unchanged prompts, fresh sessions, one worker).
All four were blocked by provider authentication before model inference.
There were zero completed model turns; automatic OWG invocation and product
value remain unmeasured. These failures are not negative adoption scores.
The 54-case reconstruction corpus validator and synthetic raw-recall
diagnostic also ran successfully; neither establishes real-agent behavior.

## Earlier archived attempts

The October 10 CLI experiments were **inconclusive**: 192 submitted attempts
across original/preliminary and corrected reruns; **zero completed model
inference turns** because of provider authentication failures. Real OAuth/MCP
transport tests succeeded, but automatic invocation and agent work-quality
improvement remain **unmeasured**.

Keep the interpretive [REPORT.md](REPORT.md) and
[FIXTURE_FIX_REPORT.md](FIXTURE_FIX_REPORT.md) in this active repository. To
reduce product-repository noise, per-attempt JSONL, catalog dumps, empty
traces, routing summaries and ephemeral fixture snapshots were removed from
the current tree. They remain retrievable from Git history at commit
`efe22a0d906cb8baad63b453e6ff4df50af00292`:

- [Original validated trial artifacts](https://github.com/KAVentures/openworkgraph/tree/efe22a0d906cb8baad63b453e6ff4df50af00292/evals/hosted_context/results/2026-10-10-cli-validated)
- [Preliminary attempts](https://github.com/KAVentures/openworkgraph/tree/efe22a0d906cb8baad63b453e6ff4df50af00292/evals/hosted_context/results/2026-10-10-cli)
- [Fixture correction rerun](https://github.com/KAVentures/openworkgraph/tree/efe22a0d906cb8baad63b453e6ff4df50af00292/evals/hosted_context/results/2026-10-10-fixture-rerun)

The synthetic fixtures, code, tests, reports and dependency lock remain.
Future model outputs belong in private ignored or external directories, not
in the user-facing source/install package. See [the protocol](../README.md).
