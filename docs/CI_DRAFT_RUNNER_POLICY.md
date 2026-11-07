# Draft PR CI budget — full validation at ready-for-review

OWG runs five continuous development workers and frequently has many open
draft PRs. On 7 November 2026, **14 of 26** open PRs were drafts. The six
full CI workflows were still allocating Ubuntu, Windows, macOS, Chromium,
container and packaging runners for every intermediate draft commit. Frequent
worker pushes canceled prior attempts and immediately filled the queue again.

## What runs when?

| Event | Draft smoke | Six full workflows |
|---|---|---|
| Open/update/reopen **draft PR** | Python + JS syntax | All expensive jobs skipped |
| Convert PR to **ready for review** | Skipped | **Full required native and browser CI** |
| Push new commits to ready PR | Skipped | Full required CI re-runs |
| Push to `main` | N/A | Full CI runs |
| Manually dispatch supported workflow on chosen branch | N/A | Full workflow runs |

The full required workflows are `tests`, `scale-acceptance`,
`explorer-accessibility`, `starter-runtimes`,
`packaged-launch-smoke`, and `tester-release`. Every full workflow now
explicitly reacts to the `ready_for_review` PR event (in addition to opening,
pushing and reopening). Every expensive job checks the current PR draft flag.
The release publishing job keeps its existing main-only condition.

`draft-smoke.yml` is an intentionally lightweight, unprivileged check:
Python syntax compilation, dashboard/extension/browser JavaScript syntax,
and no network downloads other than the hosted Python/Node toolchains.
It does not import customer data, run agents, deploy, publish, grant
permissions, or claim performance/behavioral correctness. Development workers
can request full pre-ready validation via manual `workflow_dispatch` from
the Actions tab without removing the draft flag.

## Merge safety

**No merge criteria are relaxed.** The independently guarded merge
coordinator in `.github/scripts/merge_on_green.cjs` already refuses:

- draft PRs or missing exact-head explicit opt-in;
- stale branches that do not include current `main`;
- incomplete/failed/skipped six-workflow exact-head CI;
- missing or skipped native Windows/macOS/Ubuntu pytest and other required jobs;
- outstanding review or unresolved threads.

The coordinator itself is not modified by this change. A draft PR's skipped
full jobs cannot satisfy the gate. When the author marks a PR ready, all six
workflows and native jobs run on the same exact head, allowing safe final
review and merge. No branch protection is removed or replaced.

## Operational acceptance

`tests/test_pr_draft_ci_gate.py` verifies all six full workflow triggers
contain `ready_for_review`, jobs are guarded against drafts, main pushes
are retained, and the exact six-group/seven-job merge policy remains intact.
The PR requires its own exact-head native/browser/packaging checks before
merging to `main`; a further staged draft-to-ready GitHub PR observation
should confirm actual hosted-runner consumption.

This reduces *avoidable* job scheduling. It cannot itself repair account
billing/spending limits, GitHub-hosted runner outages, or workflow failures
inside full test suites. Those must still be diagnosed from GitHub Actions.
