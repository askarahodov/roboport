import {test} from 'node:test';
import assert from 'node:assert/strict';
import {activeRefs,gateGreen,disposition} from '../../scripts/branch_hygiene.mjs';

const fullName='owner/repo',sha='a'.repeat(40),merge='b'.repeat(40);
const branch={name:'feature/issue-1-demo',commit:{sha},protected:false};
const pr={number:1,merged_at:'2026-11-27T00:00:00Z',merge_commit_sha:merge,
  head:{ref:branch.name,sha,repo:{full_name:fullName}},
  base:{ref:'main',repo:{full_name:fullName}}};
const options={fullName,defaultBranch:'main',active:new Set(),closed:[pr],ancestor:x=>x===merge};
test('exact merged main tip and reachable merge are eligible',()=>{
  assert.deepEqual(disposition(branch,options),{reason:'DELETE_CANDIDATE',pr:1,sha});
});
test('protected, release, hotfix, main, changed, orphan, foreign, unmerged and unreachable preserved',()=>{
  for(const b of [{...branch,protected:true},{...branch,name:'main'},{...branch,name:'release/v1'},
    {...branch,name:'hotfix/v1'},{...branch,commit:{sha:'c'.repeat(40)}}])
    assert.notEqual(disposition(b,options).reason,'DELETE_CANDIDATE');
  for(const changed of [{closed:[]},{closed:[{...pr,merged_at:null}]},
    {closed:[{...pr,base:{...pr.base,ref:'develop'}}]},
    {closed:[{...pr,head:{...pr.head,repo:{full_name:'fork/repo'}}}]},{ancestor:()=>false}])
    assert.notEqual(disposition(branch,{...options,...changed}).reason,'DELETE_CANDIDATE');
  assert.equal(disposition(branch,{...options,closed:[pr,{...pr,merged_at:null,closed_at:'2026-11-28'}]}).reason,
    'CLOSED_UNMERGED_REVIEW_REQUIRED');
});
test('open PR head and base including fork base preserved',()=>{
  for(const open of [[pr],[{head:{ref:'fork-feature',repo:{full_name:'fork/repo'}},
    base:{ref:branch.name,repo:{full_name:fullName}}}]]){
    assert.equal(disposition(branch,{...options,active:activeRefs(open,fullName)}).reason,'OPEN_PR_HEAD_OR_BASE');
  }
});
test('main CI requires exact SHA, repository and six successful jobs; skip is not success',()=>{
  const run={head_sha:sha,head_branch:'main',head_repository:{full_name:fullName},event:'push',status:'completed',conclusion:'success'};
  const jobs=['impact','docs','postgres','api','web','browser-e2e'].map(name=>({name,status:'completed',conclusion:'success'}));
  assert.equal(gateGreen(run,jobs,sha,fullName),true);
  assert.equal(gateGreen(run,jobs.slice(1),sha,fullName),false);
  assert.equal(gateGreen(run,[...jobs,{...jobs[0]}],sha,fullName),false);
  assert.equal(gateGreen(run,jobs.map((j,i)=>i?j:{...j,conclusion:'skipped'}),sha,fullName),false);
  assert.equal(gateGreen({...run,event:'pull_request'},jobs,sha,fullName),false);
  assert.equal(gateGreen(run,jobs,merge,fullName),false);
});


test('one-time reviewed ancestor permits only exact repository and SHA, still honors protected/open refs',()=>{
  const reviewed={name:'feature/issue-130-safe-vehicle-location-transfer',
    commit:{sha:'e2f13430d91326723fe468a1224c92dbec063b96'},protected:false};
  const opts={...options,fullName:'askarahodov/roboport',closed:[],ancestor:x=>x===reviewed.commit.sha};
  assert.deepEqual(disposition(reviewed,opts),{reason:'DELETE_CANDIDATE',pr:null,sha:reviewed.commit.sha});
  for(const changed of [{fullName:'fork/repo'},{ancestor:()=>false},{active:new Set([reviewed.name])}])
    assert.notEqual(disposition(reviewed,{...opts,...changed}).reason,'DELETE_CANDIDATE');
  assert.notEqual(disposition({...reviewed,protected:true},opts).reason,'DELETE_CANDIDATE');
  assert.notEqual(disposition({...reviewed,commit:{sha}},opts).reason,'DELETE_CANDIDATE');
  assert.notEqual(disposition({...reviewed,name:'feature/unreviewed'},opts).reason,'DELETE_CANDIDATE');
});
