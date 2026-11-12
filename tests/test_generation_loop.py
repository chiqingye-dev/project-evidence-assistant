"""Actual fixture Git/store/task loop; no real model or original ledger."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter, AgentBudget
from project_evidence.generation_loop import GenerationSession
from project_evidence.section_semantic_review import HostReviewer
from test_m0_api import repository, git_exe
from test_coverage_loop import policy_fixture
from test_section_review_api import actions, prepare
from test_section_semantic_review import contract, assessment
from test_agent_api import terminal

TEXT='已读条件与异常来自静态声明，运行效果尚未验证。'


def policy_and_script(repository, git_exe, *, missing=False, budget=False):
    root, controller, commit, transport, policy=policy_fixture(repository,git_exe)
    policy=replace(policy,public_objectives=('依实际读取来源解释所选小节并保留证据边界。',))
    spec=contract(policy);script=actions(TEXT);finish=script.pop();views=[]
    def plan(context):
        row=next(h['host_generation_coverage'] for h in context.history if 'host_generation_coverage' in h)
        assert row['stage']=='planning' and all(r not in json.dumps(context.model_dump(),ensure_ascii=False) for r in spec.required)
        views.append(deepcopy(row))
        return dict(kind='section_plan',section_plan=dict(criteria=[dict(number=1,targets=['f:0'],gap='')]),
                    usage=dict(mode='scripted',input_tokens=10,output_tokens=context.remaining.output_tokens if budget else 30))
    def final(context):
        row=next(h['host_generation_coverage'] for h in context.history if 'host_generation_coverage' in h)
        assert row['stage']=='prose' and len(row['plan_digest'])==64
        assert sum('host_source_coverage' in h for h in context.history)==1
        value=finish(context)
        if not missing:value['coverage_claims']=[dict(number=1,target='f:0',field='block:1:text',quote=TEXT,kind='explanation')]
        return value
    script += [plan,final]
    return root,controller,commit,transport,policy,spec,script,views


@pytest.mark.parametrize('missing',[False,True])
def test_plan_is_persisted_before_prose_and_exact_held_report_vetoes_false_positive_review(tmp_path,repository,git_exe,missing):
    root,_,commit,transport,policy,spec,script,views=policy_and_script(repository,git_exe,missing=missing)
    box={};finish=script[-1]
    def final(context):
        service=box['service'];task=service.history.get(service._active_project,service._active_task)
        events=[e for e in task['events'] if e['kind']=='coverage_plan_accepted']
        assert len(events)==1 and events[0]['plan_digest']==next(h['host_generation_coverage']['plan_digest'] for h in context.history if 'host_generation_coverage' in h)
        return finish(context)
    script[-1]=final;adapter=ScriptedAdapter(script)
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy,review_contract=spec),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service;box['service']=service
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status']=='needs_review' and len(views)==1 and adapter.calls==5
        assert task['counters']['model_calls']==3 and task['counters']['tool_calls']==1
        held=service.held_reviews.get(task['project_id'],task['id']);before=deepcopy(task['result'])
        report=held['generation_coverage_report']
        assert bool(report['structural_blockers']) is missing
        assert report['output_digest']==held['output_digest'] and not report['publication_approval']
        assert all(r not in json.dumps(held['generation_plan'],ensure_ascii=False) for r in spec.required)
        receipt=service.review_held(service.projects.get(task['project_id']),task['id'],HostReviewer('fixture','scripted',assessment))
        assert receipt['publication_performed'] is (not missing)
        assert receipt['generation_coverage_report']==report
        assert len(client.get(route+'/learning').json())==(1 if missing else 2)
        assert service.history.get(task['project_id'],task['id'])['result']==before


@pytest.mark.parametrize('case',['cancel','version','session','context','event','tokens','turns','time','late_read','no_plan'])
def test_plan_and_prose_share_original_guards_clock_and_receipts(tmp_path,repository,git_exe,monkeypatch,case):
    root,_,commit,transport,policy,spec,script,_=policy_and_script(repository,git_exe,budget=case=='tokens')
    if case=='no_plan':script.pop(-2)
    if case=='late_read':script[-1]=script[2]
    box={};original=GenerationSession.context
    def context(self,refs):
        value=original(self,refs)
        if value['host_generation_coverage']['stage']=='prose':
            service=box['service'];record=service.projects.get(service._active_project)
            if case=='cancel':service.cancel(record,service._active_task)
            if case=='version':service.projects.set_version(record.id,'b'*40)
            if case=='session':service.sources.clear(record.id)
            if case=='context':value['pressure']='x'*24000
        return value
    monkeypatch.setattr(GenerationSession,'context',context)
    if case=='time':
        import project_evidence.agent_engine as engine
        accept=GenerationSession.accept;clock=engine.time.monotonic
        def late(self,*a):
            value=accept(self,*a);monkeypatch.setattr(engine,'time',SimpleNamespace(monotonic=lambda:clock()+31));return value
        monkeypatch.setattr(GenerationSession,'accept',late)
    adapter=ScriptedAdapter(script)
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy,review_contract=spec),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service;box['service']=service
        if case=='turns':service.limits=AgentBudget(max_turns=2,max_retries=0)
        if case=='event':
            update=service.history.update
            def fail(*a,**k):
                if k.get('event',{}).get('kind')=='coverage_plan_accepted':return False
                return update(*a,**k)
            monkeypatch.setattr(service.history,'update',fail)
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status'] not in ('completed','needs_review')
        expected=dict(cancel='cancelled',version='source_changed',session='unverified',context='budget_exhausted',event='failed',tokens='budget_exhausted',turns='budget_exhausted',time='timeout',late_read='failed',no_plan='failed')
        assert task['status']==expected[case]
        assert adapter.calls==(5 if case=='late_read' else 4)
        assert task['counters']['tool_calls']==1
        if case=='tokens':assert task['counters']['output_tokens']==4000
        assert len(client.get(route+'/learning').json())==1 and not service._held_live


@pytest.mark.parametrize('case',['facets','contract','public_json','public_duplicate','protocol'])
def test_planning_requires_explicit_host_policy_before_any_model(tmp_path,repository,git_exe,case):
    root,_,commit,transport,policy,spec,_,_=policy_and_script(repository,git_exe)
    if case=='facets':policy=replace(policy,coverage_facets=())
    if case=='contract':spec=None
    if case=='public_json':policy=replace(policy,public_objectives=['Goal'])
    if case=='public_duplicate':policy=replace(policy,public_objectives=('Goal','Goal'))
    adapter=ScriptedAdapter([])
    if case=='protocol':adapter=SimpleNamespace(kind='real',section_protocol='json_object',calls=0)
    with pytest.raises(ValueError):create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy,review_contract=spec)
    assert adapter.calls==0


def test_explicit_unknown_is_kept_for_independent_review_instead_of_forced_into_fact(tmp_path,repository,git_exe):
    root,_,commit,transport,policy,spec,script,_=policy_and_script(repository,git_exe)
    gap='尚未读取接收方实现，因此保留证据缺口。'
    script[-2]=dict(kind='section_plan',section_plan=dict(criteria=[dict(number=1,targets=[],gap='Receiver implementation unread.')]))
    finish=script[-1]
    def final(context):
        value=finish(context);value['final_output']['gaps']=[gap]
        value['coverage_claims']=[dict(number=1,target=None,field='gap:1',quote=gap,kind='unknown')]
        return value
    script[-1]=final
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=ScriptedAdapter(script),section_review_policy=policy,review_contract=spec),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status']=='needs_review'
        report=service.held_reviews.get(task['project_id'],task['id'])['generation_coverage_report']
        assert report['findings'] and not report['structural_blockers']
        assert not report['semantic_verified'] and not report['publication_approval']
