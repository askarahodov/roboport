import {execFileSync} from 'node:child_process';

const REQUIRED = ['impact','docs','postgres','api','web','browser-e2e'];
const reserved = new Set(['main','master','develop','development','staging','production']);
const shaPattern = /^[0-9a-f]{40}$/;
const temporary = /^(feature|fix|chore|tmp)\/[A-Za-z0-9][A-Za-z0-9._/-]*$/;

export function activeRefs(open, fullName) {
  const refs = new Set();
  for (const pr of open) {
    if (pr.head?.repo?.full_name === fullName) refs.add(pr.head.ref);
    if (pr.base?.repo?.full_name === fullName) refs.add(pr.base.ref);
  }
  return refs;
}

export function gateGreen(run, jobs, sha, fullName) {
  return !!run && run.head_sha === sha && run.head_branch === 'main' &&
    run.head_repository?.full_name === fullName &&
    ['push','workflow_dispatch'].includes(run.event) &&
    run.status === 'completed' && run.conclusion === 'success' &&
    REQUIRED.every(name => {
      const matching = jobs.filter(job => job.name === name);
      return matching.length === 1 && matching[0].status === 'completed' &&
        matching[0].conclusion === 'success';
    });
}

export function disposition(branch, {fullName, defaultBranch, active, closed, ancestor}) {
  const name = branch.name, sha = branch.commit?.sha;
  if (branch.protected || reserved.has(name) || name === defaultBranch ||
      !temporary.test(name) || !shaPattern.test(sha ?? '')) return {reason:'PROTECTED_OR_NON_TEMPORARY'};
  if (active.has(name)) return {reason:'OPEN_PR_HEAD_OR_BASE'};
  const related = closed.filter(pr => pr.head?.repo?.full_name === fullName &&
    pr.head.ref === name);
  const merged = related.find(pr => pr.merged_at && pr.base?.repo?.full_name === fullName &&
    pr.base.ref === 'main' && pr.head.sha === sha &&
    shaPattern.test(pr.merge_commit_sha ?? '') && ancestor(pr.merge_commit_sha));
  if (!merged) return {reason:related.length ? 'NO_EXACT_MERGED_MAIN_PROOF' : 'ORPHAN_REVIEW_REQUIRED'};
  // A newer unmerged PR with this exact ref is evidence the branch is in reuse.
  if (related.some(pr => !pr.merged_at && pr.head.sha === sha &&
      String(pr.closed_at ?? '') >= String(merged.merged_at))) return {reason:'CLOSED_UNMERGED_REVIEW_REQUIRED'};
  return {reason:'DELETE_CANDIDATE',pr:merged.number,sha};
}

function git(args) {
  return execFileSync('git',args,{encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
}
function reachable(merge) {
  try { git(['merge-base','--is-ancestor',merge,'HEAD']); return true; }
  catch { return false; }
}

export async function runHygiene({github,context,core,dryRun}) {
  const {owner,repo}=context.repo;
  const args={owner,repo};
  const repository=(await github.rest.repos.get(args)).data;
  const fullName=repository.full_name;
  const readMain=async()=> (await github.rest.git.getRef({...args,ref:'heads/main'})).data.object.sha;
  const readOpen=()=>github.paginate(github.rest.pulls.list,{...args,state:'open',per_page:100});
  const main=await readMain();
  const local=git(['rev-parse','HEAD']);
  if (repository.default_branch !== 'main' || local !== main) {
    core.warning('Main changed or checkout is not default main; no deletion.');
    return;
  }
  const runs=(await github.rest.actions.listWorkflowRuns({...args,workflow_id:'ci.yml',branch:'main',per_page:100})).data.workflow_runs;
  const run=runs.find(x=>x.head_sha===main && x.head_repository?.full_name===fullName &&
    ['push','workflow_dispatch'].includes(x.event));
  const jobs=run ? await github.paginate(github.rest.actions.listJobsForWorkflowRun,
    {...args,run_id:run.id,per_page:100}) : [];
  const green=gateGreen(run,jobs,main,fullName);
  const auditOnly=dryRun || !green;
  const open=await readOpen();
  const active=activeRefs(open,fullName);
  const branches=await github.paginate(github.rest.repos.listBranches,{...args,per_page:100});
  const closed=await github.paginate(github.rest.pulls.list,{...args,state:'closed',per_page:100});
  const lines=[]; let candidates=0,deleted=0,failed=0;
  for (const branch of branches) {
    const result=disposition(branch,{fullName,defaultBranch:repository.default_branch,active,closed,ancestor:reachable});
    const name=branch.name;
    if (result.reason !== 'DELETE_CANDIDATE') {
      lines.push(name+' '+branch.commit.sha+' '+result.reason); continue;
    }
    candidates++;
    if (auditOnly) { lines.push(name+' '+result.sha+' DRY_RUN PR #'+result.pr); continue; }
    try {
      // Recheck branch/head/base and current main immediately before the leased push.
      if (await readMain()!==main || activeRefs(await readOpen(),fullName).has(name)) {
        lines.push(name+' PRESERVED_CHANGED_MAIN_OR_ACTIVE_PR'); continue;
      }
      const live=(await github.rest.repos.getBranch({...args,branch:name})).data;
      if (live.protected || live.commit.sha!==result.sha || await readMain()!==main) {
        lines.push(name+' PRESERVED_CHANGED_OR_PROTECTED'); continue;
      }
      const ref='refs/heads/'+name;
      git(['check-ref-format',ref]);
      git(['push','--force-with-lease='+ref+':'+result.sha,'origin',':'+ref]);
      deleted++; lines.push(name+' '+result.sha+' DELETED PR #'+result.pr);
    } catch (error) {
      failed++; lines.push(name+' '+result.sha+' PRESERVED_DELETE_FAILED');
      core.warning('Branch deletion not confirmed (permission, lease or protection): '+name);
    }
  }
  if (open.length>2) core.warning('Open PR count '+open.length+' exceeds WIP target; triage before new product PRs.');
  core.summary.addHeading('Gitflow branch hygiene')
    .addRaw('Main: '+main+'; full CI: '+(green?'GREEN':'NOT VERIFIED')+'; mode: '+(auditOnly?'audit':'delete')+'\n\n')
    .addRaw('Open PRs: '+open.length+'; remote branches: '+branches.length+'; candidates: '+candidates+
      '; deleted: '+deleted+'; failed/preserved: '+failed+'\n\n')
    .addCodeBlock(lines.join('\n')).write();
  if (failed) core.setFailed('Some candidate deletions could not be confirmed; inspect audit.');
}
