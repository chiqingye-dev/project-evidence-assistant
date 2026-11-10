"""Actual TaskService + fixture Git + scripted adapter; zero live model claims."""
from dataclasses import replace
from hashlib import sha1,sha256
from threading import Event
import pytest
from fastapi.testclient import TestClient
from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter,OUTLINE_GOAL,SECTION_GOAL
from project_evidence.call_binding import FileSeal
from project_evidence.section_review import ReviewPolicy,AcceptedReads
from test_m0_api import repository,git_exe,register,git
from test_learning_api import parent,steps,PATH
from test_agent_api import terminal
from test_learning_core import explanation


def fixture_policy(repository,git_exe):
    root,_,controller,commits,transport=repository
    controller.write_text('class DemoController {\n void hello() {\n  sink(1);\n }\n}\n',encoding='utf-8')
    git(git_exe,root,'add','src/main/java/example/DemoController.java')
    git(git_exe,root,'commit','-qm','Call syntax fixture')
    commit=git(git_exe,root,'rev-parse','HEAD');commits[commit]=git(git_exe,root,'rev-parse','HEAD^{tree}')
    raw=controller.read_bytes()
    policy=ReviewPolicy('https://github.com/example/public-java',commit,'query','entry',
        (FileSeal(PATH,sha256(raw).hexdigest(),sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),((1,5),)),))
    return root,controller,commit,transport,policy


def actions(text,block=None):
    def finish(context):
        if block:block[0].set();assert block[1].wait(5)
        body=explanation(context.observations[-1].result['evidence_id'])
        body['blocks'][0]['text']=text
        return dict(kind='final',final_output=body)
    return steps(OUTLINE_GOAL)+[steps(SECTION_GOAL)[0],finish]


def prepare(client,root,commit):
    route=register(client,root,commit);did=parent(client,route)
    assert client.post(route+'/source/verify').json()['status']=='verified'
    task=client.post(route+'/learning/outline',json=dict(directory_task_id=did,chapter_index=0)).json()
    assert terminal(client,route,task['id'])['status']=='completed'
    outline=client.get(route+'/learning').json()[0]
    return route,outline


@pytest.mark.parametrize('text',['sink 是这里已读到的调用。','sink 传入回调并立即执行。'])
def test_section_is_frozen_and_withheld_with_real_accepted_reads_even_without_findings(tmp_path,repository,git_exe,text):
    root,controller,commit,transport,policy=fixture_policy(repository,git_exe);original=controller.read_bytes()
    data=tmp_path/'agent';adapter=ScriptedAdapter(actions(text))
    with TestClient(create_app(data,github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service
        source=service.sources.source(service.projects.get(route.rsplit('/',1)[-1]))
        reads=[];read=source.read_bytes
        def counted(path):reads.append(path);return read(path)
        source.read_bytes=counted
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        task=terminal(client,route,task['id'])
        assert task['status']=='needs_review' and task['result']['blocks'][0]['text']==text
        assert task['candidate_review']['publication_approval'] is task['candidate_review']['semantic_verified'] is False
        assert client.get(route+'/learning').json()==[outline]
        frozen=service.held_reviews.get(task['project_id'],task['id'])
        assert frozen['output']==task['result'] and frozen['index']['task_id']==task['id']
        assert len(frozen['index']['accepted_reads'])==1
        assert [c['callee'] for s in frozen['index']['sources'].values() for c in s['calls']]==['sink']
        assert reads==[PATH] and adapter.calls==4
        if '回调' in text:assert frozen['audit']['bounded_body_audit']['findings']
        else:assert frozen['audit']['status']=='syntax_supported_only'
        assert client.post(route+'/tasks/'+task['id']+'/approve',json={}).status_code==404
    with TestClient(create_app(data),base_url='http://127.0.0.1') as reopened:
        archived=reopened.get(route+'/tasks/'+task['id']).json()
        assert archived['status']=='needs_review' and archived['candidate_review']==task['candidate_review']
        assert reopened.get(route+'/learning').json()[0]['id']==outline['id']
        assert len(reopened.get(route+'/learning').json())==1
    assert controller.read_bytes()==original


@pytest.mark.parametrize('case',['cancel','version','session','store_error','size','scope','seal'])
def test_late_invalid_or_unstorable_candidate_cannot_publish(tmp_path,repository,git_exe,monkeypatch,case):
    root,_,commit,transport,policy=fixture_policy(repository,git_exe)
    entered,release=Event(),Event();adapter=ScriptedAdapter(actions('sink 传入回调。',(entered,release)))
    if case=='scope':policy=replace(policy,files=(replace(policy.files[0],spans=((3,3),)),))
    if case=='seal':policy=replace(policy,files=(replace(policy.files[0],sha256='0'*64),))
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service
        if case=='store_error':
            def fail(*_):raise RuntimeError('synthetic failure')
            monkeypatch.setattr(service.held_reviews,'save',fail)
        if case=='size':
            freeze=AcceptedReads.freeze
            monkeypatch.setattr(AcceptedReads,'freeze',lambda self,*a,**k:freeze(self,*a,max_bytes=1))
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        try:
            if case not in ('scope','seal'):
                assert entered.wait(5)
                if case=='cancel':client.post(route+'/tasks/'+task['id']+'/cancel')
                if case=='version':client.patch(route+'/version',json=dict(commit='b'*40))
                if case=='session':service.sources.clear(task['project_id'])
        finally:release.set();service._thread.join(5)
        stopped=client.get(route+'/tasks/'+task['id']).json()
        expected=dict(cancel='cancelled',version='source_changed',session='unverified',store_error='failed',size='budget_exhausted',scope='denied',seal='source_changed')[case]
        assert stopped['status']==expected
        if case in ('store_error','size'):
            assert stopped['result']['blocks'][0]['text']=='sink 传入回调。'
            assert stopped['quality_verified'] is False
        else:assert stopped['result'] is None
        assert service.held_reviews.get(task['project_id'],task['id']) is None
        assert len(client.get(route+'/learning').json())==1
        if case in ('scope','seal'):assert adapter.calls==3


def test_http_cannot_select_policy_or_override_holding(tmp_path):
    with TestClient(create_app(tmp_path/'agent'),base_url='http://127.0.0.1') as client:
        route=register(client,tmp_path/'unopened','a'*40)
        rejected=client.post(route+'/learning/section',json=dict(outline_id='none',section_id='entry',section_review_policy={},publication_approval=True))
        assert rejected.status_code==422
        assert client.get(route+'/tasks').json()==[]


def test_mismatched_host_policy_blocks_before_task_creation_or_section_model_call(tmp_path,repository,git_exe):
    root,_,commit,transport,policy=fixture_policy(repository,git_exe)
    policy=replace(policy,section_id='different')
    adapter=ScriptedAdapter(actions('unused'))
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);before=client.get(route+'/tasks').json()
        response=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry'))
        assert response.json()['status']=='denied'
        assert client.get(route+'/tasks').json()==before and adapter.calls==2
