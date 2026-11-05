'use strict';

/** Opt-in PR merge coordinator. Executed ONLY from trusted default-branch code.
 *
 * Never checkout PR head or evaluate PR supplied scripts. A contributor with
 * same-repo write access must add an exact-HEAD opt-in token to the PR body:
 *   <!-- OWG-MERGE-ON-GREEN:<40-hex-head-sha> -->
 *
 * Drafts, unresolved reviews, missing exact-head CI, forks, and unstable
 * mergeability all fail closed. This is not a replacement for branch rules.
 */
const REQUIRED_WORKFLOWS = Object.freeze([
  'tests', 'scale-acceptance', 'explorer-accessibility',
  'starter-runtimes', 'packaged-launch-smoke', 'tester-release',
]);
const REQUIRED_TEST_JOBS = Object.freeze([
  'pytest (ubuntu-latest, 3.11)',
  'pytest (macos-latest, 3.11)',
  'pytest (windows-latest, 3.11)',
  'process-collaboration',
  'browser-js',
  'gateway-container',
  'chatgpt-plugin-container',
]);
const OPT_IN_PREFIX = '<!-- OWG-MERGE-ON-GREEN:';

function exactOptIn(pr) {
  return String(pr.body || '').includes(OPT_IN_PREFIX + pr.head.sha + ' -->');
}

function latestRunsForHead(runs, sha) {
  const grouped = new Map();
  for (const run of runs || []) {
    if (run.head_sha !== sha || run.event !== 'pull_request' ||
        !REQUIRED_WORKFLOWS.includes(run.name)) continue;
    const current = grouped.get(run.name);
    if (!current || Number(run.id) > Number(current.id) ||
        (run.id === current.id && Number(run.run_attempt) > Number(current.run_attempt))) {
      grouped.set(run.name, run);
    }
  }
  return grouped;
}

function latestReviewStates(reviews) {
  const state = new Map();
  for (const review of reviews || []) {
    const author = review.user?.login;
    if (!author || !['APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'].includes(review.state)) continue;
    state.set(author, review.state);
  }
  return state;
}

function decision({pr, owner, repo, workflowRuns, testJobs, reviewThreads, hasMoreReviewThreads, reviews}) {
  if (!pr || pr.state !== 'open' || pr.number === 1) return 'closed-or-excluded';
  if (pr.base?.ref !== 'main' || pr.base?.repo?.full_name !== owner + '/' + repo ||
      pr.head?.repo?.full_name !== owner + '/' + repo) return 'cross-repository-or-base';
  if (pr.draft || !exactOptIn(pr)) return 'draft-or-not-explicitly-opted-in';
  if (pr.mergeable !== true || pr.mergeable_state !== 'clean') return 'not-cleanly-mergeable';
  if ((pr.requested_reviewers || []).length || (pr.requested_teams || []).length)
    return 'review-still-requested';
  if (hasMoreReviewThreads || (reviewThreads || []).some(t => !t.isResolved))
    return 'unresolved-or-unread-review-threads';
  if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED'))
    return 'changes-requested';
  const workflows = latestRunsForHead(workflowRuns, pr.head.sha);
  for (const name of REQUIRED_WORKFLOWS) {
    const run = workflows.get(name);
    if (!run || run.status !== 'completed' || run.conclusion !== 'success')
      return 'incomplete-exact-head-workflow:' + name;
  }
  const jobNames = new Map((testJobs || []).map(j => [j.name, j]));
  for (const name of REQUIRED_TEST_JOBS) {
    const job = jobNames.get(name);
    if (!job || job.status !== 'completed' || job.conclusion !== 'success')
      return 'incomplete-native-test-job:' + name;
  }
  return null;
}

async function run({github, context, core}) {
  const {owner, repo} = context.repo;
  const signal = context.payload.workflow_run;
  if (signal && (signal.event !== 'pull_request' || signal.conclusion !== 'success' ||
                 signal.head_repository?.full_name !== owner + '/' + repo)) {
    core.info('Ignoring unrelated or unsuccessful workflow completion.');
    return;
  }
  let ids = [];
  if (signal) {
    // Re-query source of truth: the workflow payload can omit associated PRs.
    const related = await github.rest.repos.listPullRequestsAssociatedWithCommit({
      owner, repo, commit_sha: signal.head_sha,
    });
    ids = related.data.filter(p => p.state === 'open' && p.head?.sha === signal.head_sha)
                      .map(p => p.number);
  } else if (context.eventName === 'pull_request_target') {
    const item = context.payload.pull_request;
    if (item) ids = [item.number];
  }
  for (const number of [...new Set(ids)]) {
    const response = await github.rest.pulls.get({owner, repo, pull_number: number});
    const pr = response.data;
    if (pr.draft || !exactOptIn(pr) || pr.state !== 'open') {
      core.info('PR #' + number + ': no current-head merge approval or still draft.');
      continue;
    }
    if (pr.base?.ref !== 'main' || pr.head?.repo?.full_name !== owner + '/' + repo) continue;
    const runs = await github.paginate(github.rest.actions.listWorkflowRunsForRepo, {
      owner, repo, branch: pr.head.ref, event: 'pull_request', per_page: 100,
    });
    const selected = latestRunsForHead(runs, pr.head.sha);
    const testRun = selected.get('tests');
    const jobs = testRun ? await github.paginate(github.rest.actions.listJobsForWorkflowRun, {
      owner, repo, run_id: testRun.id, per_page: 100,
    }) : [];
    const rv = await github.paginate(github.rest.pulls.listReviews, {
      owner, repo, pull_number: number, per_page: 100,
    });
    const data = await github.graphql(`query($owner:String!,$repo:String!,$number:Int!) {
      repository(owner:$owner,name:$repo) { pullRequest(number:$number) {
        reviewThreads(first:100) { pageInfo { hasNextPage } nodes { isResolved } }
      } } }`, {owner, repo, number});
    const reviewThreads = data.repository?.pullRequest?.reviewThreads;
    if (!reviewThreads) {
      core.info('PR #' + number + ': unavailable review threads; refuse.');
      continue;
    }
    // Re-fetch immediately before evaluating and attempting merge; the exact
    // SHA is also enforced atomically by GitHub's merge API.
    const current = (await github.rest.pulls.get({owner, repo, pull_number: number})).data;
    const reason = decision({
      pr: current, owner, repo, workflowRuns: runs, testJobs: jobs,
      reviewThreads: reviewThreads.nodes,
      hasMoreReviewThreads: reviewThreads.pageInfo.hasNextPage,
      reviews: rv,
    });
    if (reason) {
      core.info('PR #' + number + ': no merge (' + reason + ')');
      continue;
    }
    const result = await github.rest.pulls.merge({
      owner, repo, pull_number: number, sha: current.head.sha, merge_method: 'squash',
    });
    core.info('Safely merged opted-in PR #' + number + ' as ' + result.data.sha);
  }
}

module.exports = {REQUIRED_WORKFLOWS, REQUIRED_TEST_JOBS, OPT_IN_PREFIX,
  exactOptIn, latestRunsForHead, latestReviewStates, decision, run};
