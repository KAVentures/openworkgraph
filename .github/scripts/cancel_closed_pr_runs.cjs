'use strict';
// Only cancel active pull_request runs unambiguously attached to a closed PR.
// This file is loaded from the trusted default branch, never from PR code.
const ACTIVE = new Set(['queued','in_progress','waiting','requested','pending']);
function closedRunCandidate(run, repositoryId, onlyNumber) {
  if (run.event !== 'pull_request' || !ACTIVE.has(run.status)) return null;
  if (!Array.isArray(run.pull_requests) || run.pull_requests.length !== 1) return null;
  const pr = run.pull_requests[0];
  if (!Number.isSafeInteger(pr.number) || pr.number <= 0 ||
      (onlyNumber != null && pr.number !== onlyNumber) ||
      pr.base?.repo?.id !== repositoryId ||
      pr.head?.repo?.id !== repositoryId ||
      !pr.head?.sha || pr.head.sha !== run.head_sha) return null;
  return pr.number;
}
async function run({github, context, core}) {
  const {owner, repo} = context.repo;
  const repositoryId = context.payload.repository?.id;
  if (!Number.isSafeInteger(repositoryId)) throw Error('Missing repository identity');
  const target = context.eventName === 'pull_request_target' ? context.payload.pull_request : null;
  if (target && (target.state !== 'closed' || target.base?.repo?.id !== repositoryId)) return;
  const dryRun = context.eventName === 'workflow_dispatch' && context.payload.inputs?.dry_run !== 'false';
  const base = {owner, repo};
  let examined = 0, cancelled = 0, errors = 0;
  const seen = new Set();
  for (const status of ACTIVE) {
    // Bound per-run API use; subsequent hourly runs scan again.
    for (let page = 1; page <= 3; page++) {
      const result = await github.rest.actions.listWorkflowRunsForRepo({
        ...base, event:'pull_request', status, per_page:100, page,
        ...(target ? {head_sha:target.head.sha} : {})
      });
      const runs = result.data.workflow_runs;
      for (const item of runs) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        const number = closedRunCandidate(item, repositoryId, target?.number);
        if (number === null) continue;
        examined++;
        try {
          const pr = (await github.rest.pulls.get({...base,pull_number:number})).data;
          if (pr.state !== 'closed' || pr.head.sha !== item.head_sha ||
              pr.head.repo?.id !== repositoryId || pr.base.repo?.id !== repositoryId) continue;
          // Don't cancel runs when the same head branch is still used by any open PR.
          const open = await github.rest.pulls.list({...base,state:'open',
            head:pr.head.repo.owner.login+':'+pr.head.ref,per_page:1});
          if (open.data.length !== 0) continue;
          const fresh = (await github.rest.actions.getWorkflowRun({...base,run_id:item.id})).data;
          if (closedRunCandidate(fresh,repositoryId,number) !== number ||
              fresh.head_sha !== item.head_sha) continue;
          const current = (await github.rest.pulls.get({...base,pull_number:number})).data;
          if (current.state !== 'closed' || current.head.sha !== item.head_sha) continue;
          if (!dryRun) await github.rest.actions.cancelWorkflowRun({...base,run_id:item.id});
          core.info((dryRun?'Would cancel ':'Cancelled ')+item.id+' (closed PR #'+number+')');
          cancelled++;
        } catch (err) {
          if (err.status !== 404 && err.status !== 409) { errors++; core.warning('Run '+item.id+': '+String(err.message)); }
        }
      }
      if (runs.length < 100) break;
    }
  }
  core.info('Examined '+examined+' candidates; '+(dryRun?'would cancel ':'cancelled ')+cancelled+'; errors '+errors);
  if(errors) core.setFailed('One or more cancellation operations failed');
}
module.exports = {run,closedRunCandidate};
