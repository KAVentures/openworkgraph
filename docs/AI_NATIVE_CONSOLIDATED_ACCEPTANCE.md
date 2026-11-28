# AI-native journey: consolidated acceptance candidate

This draft consolidates the exact latest #1663 implementation stack through
#1714, which includes #1696's main-targeted core journey, subsequent onboarding,
multi-window sampling, department campaigns and outcomes, reviewed process
knowledge, paginated MCP discovery, task/case context and matched evaluation.
It does not replace or retire any constituent branch.

At assembly, main was 375b34affa138d13685c816e55836aea3cc25f20. The exact #1714
head dc965df347733b5453480565672c9c3dbf6898ad was 105 commits ahead of main,
with 172 changed files (161 new files, no deleted files). The independently
staged procurement/analytics candidate #1711 had no overlapping changed paths
relative to that same main. This is a snapshot observation, not a guarantee of
future merge compatibility. All source branches remain unchanged.

## Acceptance

The added production-path test starts the existing enterprise runner and
compact stdio MCP server with temporary isolated stores. It exercises the
actual saved knowledge API, detail setting, history and AI access gates,
pagination across a zero-match page, hidden-term refusal under redaction,
exact reviewed knowledge retrieval, explicit task-context companion, stale
revision refusal, experimental case gate and live AI-access revocation.
All records are synthetic; no provider/model call is made.

Run:
python -m pytest -q tests/test_ai_native_mcp_knowledge_acceptance.py

The existing local retrieval/companion/evaluation helper suite was rerun:
89 passed. The full production-path regression is authored but unexecuted
locally: GitHub checkout DNS failed, the MCP dependency is unavailable, and
no loopback model server was listening. No integrated acceptance or actual
AI-performance improvement is claimed.

Before marking ready or merging:
- Recheck current main and every constituent exact head; reconcile any
  changes and coordinate with the existing #1696 and worker candidates.
- Run all required integrated CI jobs, including production-path MCP,
  full browser/application regressions and packaged runtime smoke tests.
- Do not retire constituent PRs without verifying their exact preserved
  functionality and successful integrated acceptance.
- Run actual paired external-model trials on authorized, independently
  adjudicated representative workflows; report failures and holdout results.

Keep this candidate draft while earlier CI is queued. No merge or CI bypass.
