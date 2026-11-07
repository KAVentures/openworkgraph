# Opt-in merge-on-green coordinator

The existing five scheduled OWG development workers retain their branches, responsibilities and ability to merge their *own* validated PRs. A separate **GitHub Actions worker** can merge an additional same-repository PR when its owner explicitly opts in to the precise current head SHA. This avoids consuming a sixth scheduled ChatGPT task, which is unavailable on the current plan, and avoids interrupting any worker.

Workflow: `.github/workflows/owg-merge-on-green.yml`  
Coordinator: `.github/scripts/merge_on_green.cjs`

## Owner action: opt in, never blanket auto-merge

Once the feature's implementation and tests are ready:

1. Mark its PR **Ready for review** (do not use drafts for auto-merge).
2. Add this literal HTML comment to its PR description, substituting the **full 40-character current PR head commit SHA**:
   `<!-- OWG-MERGE-ON-GREEN:0123456789abcdef0123456789abcdef01234567 -->`
3. If the branch changes, the comment is **invalid** until the owner updates it to the new SHA. Editing the PR description or marking it ready triggers a recheck. **Finishing the native `tests` workflow** is the immediate CI completion hint; all six required workflows and all seven native test jobs are still rechecked on the exact head. If packaging or scale acceptance finishes after `tests`, the **hourly GitHub Actions fallback** now actually executes and rechecks explicitly opted-in open PRs. That fallback previously had a job-condition bug despite its cron trigger being present; the job condition now includes `schedule`. This does not consume a ChatGPT automation slot or touch unapproved PRs.

This marker applies **only to that PR and that exact head**. No other worker PR is opted in by default. Existing author-operated merge paths remain available.

## Fail-closed gates

The coordinator requires **all** of the following at the time of its *fresh* GitHub API query:

- Same-repository head and base; base must be `main`, state open, not draft. Rollback PR #1 is permanently excluded.
- Current PR body has the exact-SHA marker (not an older SHA).
- Cleanly mergeable PR (`mergeable: true`, `mergeable_state: clean`) whose exact head **already includes the latest `main` commit** (fresh compare API), with no pending requested reviewers or teams, no outstanding change request and **all** review threads resolved. If thread pagination exceeds the supported 100, refuse. This blocks old green CI after another PR merges; the author must update and retest their *own* branch. Main is rechecked immediately before merge, and a changed base aborts this attempt.
- The latest run of each **six required** named workflow groups for that exact head is completed and successful: `tests`, `scale-acceptance`, `explorer-accessibility`, `starter-runtimes`, `packaged-launch-smoke`, `tester-release`.
- Within the exact-head `tests` workflow, **all seven native/required jobs** succeeded, including pytest on Ubuntu, macOS and Windows, process collaboration, browser JS, Gateway and ChatGPT plugin. Skipped, missing, queued or failed jobs block merge.
- On final merge, GitHub again checks the expected exact head SHA and the repository's own branch-protection rules. No retries that bypass or suppress checks.

All refusals are logged in Actions; the worker does not write artificial PR approvals, hide failures, rerun anyone's tests or force-push anything. The task requires write permission to *this repository* only.

## Security

The workflow runs privileged default-branch code in `workflow_run` and `pull_request_target` contexts. It **never checks out or executes the PR's files**: the checkout is pinned to the repository default branch; all PR content is treated as untrusted text, and only GitHub APIs are used. Fork PRs are excluded. The exact-SHA marker is an owner-facing explicit merge approval, not a substitute for branch protection, code review or legal/operational release authorization.

A caller whose GitHub account cannot edit a same-repository PR cannot use the marker to escalate permissions; branch protection is enforced again at the merge endpoint. An approver should never add the marker to work they have not reviewed.

## Tests and operational limits

Run `node --test tests/js/merge_on_green.test.mjs` to exercise pure gate decisions. The tests cover exact-SHA revocation, six workflow groups/seven jobs, native skips, mergeability, drafts, fork/base scope, review state and fail-closed event handling. The coordinator is intentionally **not tested by actually merging a production PR** in CI. Validate once on a disposable same-repository PR when ready, before relying on it as the only merge process.

The token is scoped to `contents:write`, `pull-requests:write`, `actions:read`. The coordinator does not maintain a background timer or manipulate existing worker schedules. It can legitimately refuse to merge a PR even if every visible check is green—for example if the branch is stale, reviews remain pending or the current mergeability state is not clean. That refusal is safer than an unsafe merge.

## Runner-load reduction

The merge coordinator now reacts to **only one** successful pull-request
workflow completion (`tests`) instead of opening six nearly identical
merge-coordinator workflows for every single contributor PR. Hourly
reconciliation catches the rare case where `tests` finished before a
slower scale, packaging or accessibility gate or GitHub had not yet
settled mergeability. The `pull_request_target` job also checks for
an **explicit marker on a non-draft PR before reserving a runner**.
It still never executes code from pull request heads and never relaxes
branch-protection rules or the six-workflow/seven-job validation gates.

The workflow contract has Python regression tests
(`tests/test_merge_on_green_workflow.py`) and a live-path Node harness
(`tests/js/merge_on_green_schedule.test.mjs`) that exercises scheduled
reconciliation, approval-only PR discovery, exact-head GitHub merge
requests, and refusal when CI or main freshness is incomplete. These tests
do not merge an actual GitHub PR or substitute for a controlled production
rollout observation.
