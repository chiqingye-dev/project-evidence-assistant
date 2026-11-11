"""Real loop/store/source pipeline using fixture Git, scripted/Mock models only."""
from dataclasses import replace
from copy import deepcopy
import json
import pytest
from fastapi.testclient import TestClient
from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter,AgentBudget,SECTION_GOAL
from project_evidence.section_review import AcceptedReads
from project_evidence.source_policy import SourceError
from project_evidence.call_binding import FileSeal
from project_evidence.section_semantic_review import HostReviewer
from test_m0_api import repository,git_exe,git
from test_section_review_api import fixture_policy,actions,prepare
from test_section_semantic_review import contract,assessment
from test_agent_api import terminal

FACETS=('branches','queries','snapshot_fields','writes','payload_response')

def policy_fixture(repository,git_exe):
    from hashlib import sha1,sha256
    root,controller,commit,transport,policy=fixture_policy(repository,git_exe)
    text='class DemoController {\n static final int LIMIT=3;\n void hello() { if (count() >= LIMIT) { throw new IllegalStateException("busy"); } sink(1); }\n int count() { return 0; } void sink(int n) {}\n}\n'
    controller.write_text(text,encoding='utf-8');git(git_exe,root,'add',str(controller.relative_to(root)));git(git_exe,root,'commit','-qm','Coverage fixture')
    commit=git(git_exe,root,'rev-parse','HEAD');repository[3][commit]=git(git_exe,root,'rev-parse','HEAD^{tree}')
    raw=controller.read_bytes();seal=FileSeal(policy.files[0].path,sha256(raw).hexdigest(),sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),((1,5),))
    return root,controller,commit,transport,replace(policy,commit=commit,files=(seal,),coverage_facets=FACETS)


def test_actual_sql_persistence_and_feedback_precede_coverage_then_hold_and_independent_release(tmp_path,repository,git_exe):
    root,controller,commit,transport,policy=policy_fixture(repository,git_exe)
    script=actions('已读条件与异常是静态代码，运行效果未验证。');finish=script[-1];box={};views=[]
    def final(context):
        service=box['service'];eid=context.observations[-1].result['evidence_id']
        with service.projects._connection() as db:assert db.execute('SELECT id FROM evidence WHERE id=?',(eid,)).fetchone()
        persisted=service.history.get(service._active_project,service._active_task)
        assert any(e['kind']=='tool_feedback_accepted' and e.get('evidence_id')==eid for e in persisted['events'])
        projected=[h['host_source_coverage'] for h in context.history if 'host_source_coverage' in h]
        assert len(projected)==1 and projected[0]['facts'] and not any('host_source_call_evidence' in h for h in context.history)
        assert len(json.dumps(context.model_dump(),ensure_ascii=False))<=24000
        views.append(projected[0]);return finish(context)
    script[-1]=final;adapter=ScriptedAdapter(script)
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy,review_contract=contract(policy)),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service;box['service']=service
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json();task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status']=='needs_review' and len(client.get(route+'/learning').json())==1 and len(views)==1
        original=deepcopy(task['result']);collector=service._held_live[task['id']][0]
        with pytest.raises(SourceError):collector.coverage_context(task['evidence'],FACETS)
        record=service.projects.get(task['project_id'])
        receipt=service.review_held(record,task['id'],HostReviewer('fixture','scripted',assessment))
        assert receipt['publication_performed'] and not receipt['semantic_verified']
        assert service.history.get(record.id,task['id'])['result']==original
        assert len(client.get(route+'/learning').json())==2


@pytest.mark.parametrize('case',['cancel','version','session','context_size','view_size','save_error','event_error'])
def test_stop_conditions_prevent_the_next_section_dispatch_or_publication(tmp_path,repository,git_exe,monkeypatch,case):
    root,_,commit,transport,policy=policy_fixture(repository,git_exe);adapter=ScriptedAdapter(actions('unused final'))
    original=AcceptedReads.coverage_context;box={};called=[]
    def projected(self,refs,facets):
        data=original(self,refs,facets)
        if not refs:return data
        called.append(True);service=box['service'];record=service.projects.get(service._active_project)
        if case=='cancel':service.cancel(record,service._active_task)
        if case=='version':service.projects.set_version(record.id,'b'*40)
        if case=='session':service.sources.clear(record.id)
        if case=='context_size':data['full_context_pressure']='x'*24000
        if case=='view_size':
            from project_evidence.source_policy import SourceError
            raise SourceError('budget_exhausted','fixture projection overflow')
        return data
    monkeypatch.setattr(AcceptedReads,'coverage_context',projected)
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service;box['service']=service
        if case=='save_error':monkeypatch.setattr(service.projects,'save_evidence',lambda *a,**k:(_ for _ in ()).throw(ValueError('fixture store failure')))
        if case=='event_error':
            update=service.history.update
            def reject(*a,**k):
                if k.get('event',{}).get('kind')=='tool_feedback_accepted':return False
                return update(*a,**k)
            monkeypatch.setattr(service.history,'update',reject)
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json();task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status'] not in ('completed','needs_review') and adapter.calls==3
        if case in ('context_size','view_size'):assert task['status']=='budget_exhausted'
        if case=='cancel':assert task['status']=='cancelled'
        if case=='version':assert task['status']=='source_changed'
        if case=='session':assert task['status']=='unverified'
        if case in ('save_error','event_error'):assert not called
        assert len(client.get(route+'/learning').json())==1 and not service._held_live


def test_oversize_after_first_read_does_not_make_second_ledger_reservation(tmp_path,monkeypatch):
    import httpx
    from project_evidence.agent_engine import run_agent
    from test_section_named_adapter import configured,response
    from test_section_native_contract import call
    from test_section_function_carrier import chosen_context
    http=[]
    def respond(request):
        http.append(1);return httpx.Response(200,json=response(call('read_excerpt',dict(path='A.java',start_line=2,end_line=3))))
    adapter,ledger=configured(tmp_path,respond);reserve=ledger.reserve;reservations=[]
    def counted(*a,**k):reservations.append(1);return reserve(*a,**k)
    monkeypatch.setattr(ledger,'reserve',counted)
    ctx=chosen_context()
    def execute(*_):return dict(status='ok',evidence_id='e1',path='A.java',start_line=2,end_line=3,sha256='a'*64,blob_oid='b'*40,commit=ctx.commit,public_url=ctx.public_url,text='sample source',truncated=False)
    result=run_agent(adapter,execute,lambda:None,lambda:False,initial_context=ctx,limits=AgentBudget(max_retries=0),host_context=lambda refs:None if not refs else dict(host_source_coverage=dict(padding='x'*24000)))
    assert result.status=='budget_exhausted' and len(http)==len(reservations)==1
    assert result.counters.retries==0 and ledger.summary('first-trial')['unresolved_requests']==0


def test_combined_view_reaches_named_adapter_only_as_data_with_mock_http(tmp_path):
    import httpx
    from project_evidence.agent_protocol import ModelContext
    from project_evidence.deepseek_adapter import DeepSeekAdapter
    from test_deepseek_adapter import setup,KEY
    from test_section_native_contract import context,call
    from test_section_named_adapter import response
    from project_evidence.strict_function_protocol import receipt_values
    from project_evidence.section_native_contract import decode
    from project_evidence.agent_protocol import Usage
    from test_source_coverage_binding import collected,PATH
    collector,refs,_=collected();data=context().model_dump()
    ref,chunk=collector._accepted['e1']
    data['observations']=[dict(call_id='read-current',name='read_excerpt',result=dict(ref,status='ok',excerpt=chunk['text']))]
    data['history']=[collector.coverage_context(refs,FACETS)];ctx=ModelContext.model_validate(data);requests=[]
    frame=response(call('report_gap',dict(message='Mock only.',gaps=['No actual provider call.'])))
    incoming,outgoing=receipt_values(frame)
    assert decode(frame,ctx,{PATH:(1,5)},Usage(input_tokens=incoming,output_tokens=outgoing,mode='real')).final_output.kind=='gap'
    def respond(request):
        payload=json.loads(request.content);requests.append(payload)
        sent=json.loads(payload['messages'][1]['content'])
        assert sent['observations']==ctx.model_dump()['observations']
        assert sent['history']==ctx.model_dump()['history'] and 'host_source_call_evidence' not in sent['history'][0]
        assert payload['max_tokens']==4000 and len(payload['tools'])==6
        return httpx.Response(200,json=frame)
    base,ledger=setup(tmp_path,respond)
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,enable_submission=True,goal=SECTION_GOAL,topic='submission',section_protocol='named_functions',section_read_ranges={PATH:(1,5)}).for_task('current-task',goal=SECTION_GOAL,topic='submission')
    assert adapter.generate(ctx,timeout_seconds=5,max_output_tokens=4000).final_output.kind=='gap'
    assert len(requests)==1 and ledger.summary('first-trial')['unresolved_requests']==0


@pytest.mark.parametrize('facets',[['branches'],('unknown',),('branches','branches'),([], )])
def test_invalid_host_facets_are_rejected_before_any_task_or_dispatch(tmp_path,facets):
    from project_evidence.storage import ProjectStore
    from project_evidence.task_service import TaskService
    from project_evidence.section_review import ReviewPolicy
    adapter=ScriptedAdapter([])
    policy=ReviewPolicy('https://github.com/example/public-java','a'*40,'query','entry',(),coverage_facets=facets)
    with pytest.raises(ValueError,match='host_coverage_facets'):TaskService(ProjectStore(tmp_path/'data'),object(),adapter,section_review_policy=policy)
    assert adapter.calls==0
