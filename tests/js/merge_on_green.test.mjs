import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const mod = require('../../.github/scripts/merge_on_green.cjs');
const sha = 'a'.repeat(40);

function pr(overrides={}) {
  return {
    number: 700, state: 'open', draft: false, mergeable: true, mergeable_state: 'clean',
    head: {sha, ref: 'feat/safe-branch', repo: {full_name: 'KAVentures/openworkgraph'}},
    base: {ref: 'main', repo: {full_name: 'KAVentures/openworkgraph'}},
    body: `<!-- OWG-MERGE-ON-GREEN:${sha} -->`,
    requested_reviewers: [], requested_teams: [], ...overrides,
  };
}
function workflows() {
  return mod.REQUIRED_WORKFLOWS.map((name, i) =>
    ({id: 100 + i, name, head_sha: sha, event: 'pull_request', status: 'completed',
      conclusion: 'success', run_attempt: 1}));
}
function jobs() {
  return mod.REQUIRED_TEST_JOBS.map((name, i) =>
    ({id: i + 200, name, status: 'completed', conclusion: 'success'}));
}
function options(overrides={}) {
  return {pr: pr(), owner: 'KAVentures', repo: 'openworkgraph',
    workflowRuns: workflows(), testJobs: jobs(), reviewThreads: [],
    hasMoreReviewThreads: false, reviews: [], ...overrides};
}
function verdict(overrides={}) {return mod.decision(options(overrides));}

test('fully green explicit same-head opt-in is eligible, including native job matrix', () => {
  assert.equal(verdict(), null);
  assert.equal(mod.REQUIRED_WORKFLOWS.length, 6);
  assert.equal(mod.REQUIRED_TEST_JOBS.length, 7);
});

test('no draft, closed, rollback, fork or unrelated base is ever merged', () => {
  assert.match(verdict({pr: pr({draft: true})}), /draft/);
  assert.match(verdict({pr: pr({number: 1})}), /closed-or-excluded/);
  assert.match(verdict({pr: pr({state: 'closed'})}), /closed-or-excluded/);
  assert.match(verdict({pr: pr({head: {...pr().head, repo: {full_name:'attacker/fork'}}})}),
    /cross-repository/);
  assert.match(verdict({pr: pr({base: {ref:'develop', repo:{full_name:'KAVentures/openworkgraph'}}})}),
    /cross-repository/);
});

test('exact SHA approval is never inherited by a new commit', () => {
  const changed = pr({head: {...pr().head, sha: 'b'.repeat(40)}});
  assert.match(verdict({pr: changed}), /not-explicitly-opted-in/);
  assert.match(verdict({pr: pr({body: '<!-- OWG-MERGE-ON-GREEN: -->'})}), /not-explicitly-opted-in/);
  assert.match(verdict({pr: pr({body: ''})}), /not-explicitly-opted-in/);
});

test('latest failed or pending run supersedes an older green one', () => {
  const runs = workflows();
  runs.push({...runs[0], id: 900, conclusion: null, status: 'in_progress'});
  assert.match(verdict({workflowRuns: runs}), /incomplete-exact-head-workflow:tests/);
  runs.at(-1).status = 'completed';
  runs.at(-1).conclusion = 'failure';
  assert.match(verdict({workflowRuns: runs}), /incomplete-exact-head-workflow:tests/);
  runs.at(-1).conclusion = 'success';
  assert.equal(verdict({workflowRuns: runs}), null);
});

test('missing workflow or one native platform test can never pass', () => {
  assert.match(verdict({workflowRuns: workflows().filter(r => r.name !== 'scale-acceptance')}),
    /scale-acceptance/);
  assert.match(verdict({testJobs: jobs().filter(j => !j.name.includes('windows-latest'))}),
    /windows-latest/);
  assert.match(verdict({testJobs: jobs().map(j => j.name.includes('macos') ?
    {...j, conclusion:'skipped'} : j)}), /macos-latest/);
});

test('review requests, outstanding threads and change requests block', () => {
  assert.match(verdict({pr: pr({requested_reviewers: [{login:'reviewer'}]})}), /review-still-requested/);
  assert.match(verdict({reviewThreads: [{isResolved:false}]}), /unresolved/);
  assert.match(verdict({hasMoreReviewThreads: true}), /unresolved/);
  assert.match(verdict({reviews: [{user:{login:'reviewer'},state:'CHANGES_REQUESTED'}]}), /changes-requested/);
  assert.equal(verdict({reviews: [
    {user:{login:'reviewer'},state:'CHANGES_REQUESTED'},
    {user:{login:'reviewer'},state:'APPROVED'},
  ]}), null);
});

test('unresolved mergeability is always a refusal, even with green CI', () => {
  assert.match(verdict({pr: pr({mergeable:false})}), /mergeable/);
  assert.match(verdict({pr: pr({mergeable_state:'unstable'})}), /mergeable/);
  assert.match(verdict({pr: pr({mergeable:null})}), /mergeable/);
});

test('run ignores unsuccessful workflow signals before contacting any PR API', async () => {
  let touched = false;
  const github = {rest: {repos: {listPullRequestsAssociatedWithCommit: async () => {
    touched = true;
    throw Error('must not be called');
  }}}};
  await mod.run({github, context: {
    repo: {owner:'KAVentures',repo:'openworkgraph'},
    payload: {workflow_run: {event:'pull_request',conclusion:'failure',
      head_repository:{full_name:'KAVentures/openworkgraph'}}},
  }, core:{info: () => {}}});
  assert.equal(touched, false);
});

test('coordinator passes exact head to GitHub merge only after refreshed gates', async () => {
  const approved = pr();
  const merges = [], notices = [];
  const listRuns = async () => {}, listJobs = async () => {}, listReviews = async () => {};
  const github = {
    rest: {
      repos: {listPullRequestsAssociatedWithCommit: async () => ({
        data: [{number:700, state:'open', head:{sha}}],
      })},
      pulls: {
        get: async () => ({data:approved}),
        listReviews,
        merge: async arg => {merges.push(arg);return {data:{sha:'merged-sha'}};},
      },
      actions: {listWorkflowRunsForRepo:listRuns, listJobsForWorkflowRun:listJobs},
    },
    paginate: async fn => fn === listRuns ? workflows() :
      fn === listJobs ? jobs() : fn === listReviews ? [] : Promise.reject(Error('unexpected endpoint')),
    graphql: async () => ({repository:{pullRequest:{reviewThreads:{
      pageInfo:{hasNextPage:false}, nodes:[],
    }}}}),
  };
  const context = {repo:{owner:'KAVentures',repo:'openworkgraph'},
    payload:{workflow_run:{
      event:'pull_request', conclusion:'success', head_sha:sha,
      head_repository:{full_name:'KAVentures/openworkgraph'},
    }}};
  await mod.run({github,context,core:{info:line=>notices.push(line)}});
  assert.equal(merges.length,1);
  assert.equal(merges[0].sha,sha);
  assert.equal(merges[0].merge_method,'squash');
  assert.equal(merges[0].pull_number,700);
  assert.ok(notices.some(line=>line.includes('Safely merged')));

  // A newer commit revokes approval without any need to reconfigure the bot.
  approved.head.sha='b'.repeat(40);
  await mod.run({github,context,core:{info:line=>notices.push(line)}});
  assert.equal(merges.length,1);
});
