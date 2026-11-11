"""Real host/task/storage pipeline with fixture Git and scripted judgements only."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import Event,Thread
import pytest
from fastapi.testclient import TestClient
from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter
from project_evidence.section_semantic_review import HostReviewer,ReviewStore,assess
from test_m0_api import repository,git_exe
from test_section_review_api import fixture_policy,actions,prepare
from test_section_semantic_review import contract,assessment
from test_agent_api import terminal


@contextmanager
def held(tmp_path,repository,git_exe,text='sink 是已读调用。'):
    root,controller,commit,transport,policy=fixture_policy(repository,git_exe)
    spec=contract(policy);steps=actions(text);finish=steps[-1]
    def projected(context):
        views=[h['host_source_call_evidence'] for h in context.history if 'host_source_call_evidence' in h]
        assert len(views)==1 and [c[2] for c in views[0]['calls']]==['sink']
        assert views[0]['calls'][0][-1]=='unknown' and not views[0]['publication_approval']
        return finish(context)
    steps[-1]=projected
    adapter=ScriptedAdapter(steps);data=tmp_path/'agent'
    app=create_app(data,github_transport=transport,git_executable=git_exe,model_adapter=adapter,
        section_review_policy=policy,review_contract=spec)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=app.state.task_service
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status']=='needs_review'
        record=service.projects.get(task['project_id'])
        yield client,service,record,route,task,outline,adapter,controller,data


def judge(function=assessment):return HostReviewer('fixture-reviewer','scripted',function)


def test_independent_review_atomically_publishes_only_the_original_and_survives_restart(tmp_path,repository,git_exe):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,controller,data):
        original=deepcopy(task['result']);source=controller.read_bytes();calls=adapter.calls
        receipt=service.review_held(record,task['id'],judge())
        assert receipt['status']=='released' and receipt['publication_performed']
        assert receipt['source_assisted_review_passed'] and receipt['review_mode']=='scripted'
        assert not receipt['semantic_verified'] and not receipt['personal_acceptance']
        saved=client.get(route+'/learning').json()
        body=next(x for x in saved if x['kind']=='section_explanation')
        assert body['content']==original and body['quality_verified'] is False
        result=client.get(route+'/tasks/'+task['id']).json()
        assert result['status']=='completed' and result['result']==original and result['quality_verified'] is False
        assert result['independent_review']['publication_performed']
        assert adapter.calls==calls and controller.read_bytes()==source
        with pytest.raises(ValueError):service.review_held(record,task['id'],judge())
        assert len(client.get(route+'/learning').json())==2
    with TestClient(create_app(data),base_url='http://127.0.0.1') as reopened:
        archived=reopened.get(route+'/tasks/'+task['id']).json()
        assert archived['independent_review']==result['independent_review']
        assert len(reopened.get(route+'/learning').json())==2


@pytest.mark.parametrize('case',['criterion','unsupported_call','critical'])
def test_unpassed_semantics_cannot_publish_even_if_other_checks_pass(tmp_path,repository,git_exe,case):
    with held(tmp_path,repository,git_exe,'sink 传入回调。' if case=='unsupported_call' else 'sink 是已读调用。') as (client,service,record,route,task,outline,adapter,*_):
        def inspect(view):
            value=assessment(view)
            if case=='criterion':value['criteria'][0]['verdict']='unverified'
            if case=='critical':value['critical_findings']=[dict(field='block:1:text',quote=view['output']['blocks'][0]['text'],reason='Unsupported fixture.')]
            return value
        receipt=service.review_held(record,task['id'],judge(inspect))
        assert receipt['status']=='review_rejected' and not receipt['publication_performed']
        archived=client.get(route+'/tasks/'+task['id']).json()
        assert archived['status']=='review_rejected' and archived['result']==task['result']
        assert client.get(route+'/learning').json()==[outline] and adapter.calls==4
        with pytest.raises(ValueError):service.review_held(record,task['id'],judge())


@pytest.mark.parametrize('case',['cancel','version','session','delete'])
def test_races_during_external_review_cannot_resurrect_or_publish(tmp_path,repository,git_exe,case):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,*_):
        entered,release=Event(),Event();errors=[]
        def inspect(view):entered.set();assert release.wait(5);return assessment(view)
        def run():
            try:service.review_held(record,task['id'],judge(inspect))
            except ValueError as error:errors.append(str(error))
        thread=Thread(target=run);thread.start()
        try:
            assert entered.wait(5)
            if case=='cancel':assert client.post(route+'/tasks/'+task['id']+'/cancel').json()['status']=='cancelled'
            if case=='version':client.patch(route+'/version',json=dict(commit='b'*40))
            if case=='session':service.sources.clear(record.id)
            if case=='delete':client.delete(route)
        finally:release.set();thread.join(5)
        assert not thread.is_alive() and errors==['review_state_changed'] and adapter.calls==4
        if case=='delete':assert client.get(route+'/tasks/'+task['id']).status_code==404
        else:
            assert len(client.get(route+'/learning').json())==1
            assert not client.get(route+'/tasks/'+task['id']).json()['independent_review']['publication_performed']


def test_invalid_review_and_publication_failure_are_preserved_without_new_model_calls(tmp_path,repository,git_exe,monkeypatch):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,*_):
        with pytest.raises(ValueError,match='invalid_review'):service.review_held(record,task['id'],judge(lambda _:dict(publication_approval=True)))
        publish=service.learning.save_from_task
        def fail(*a,**k):raise ValueError('private SQL detail must not persist')
        monkeypatch.setattr(service.learning,'save_from_task',fail)
        with pytest.raises(ValueError,match='publication_failed'):service.review_held(record,task['id'],judge())
        assert client.get(route+'/tasks/'+task['id']).json()['status']=='needs_review'
        assert client.get(route+'/learning').json()==[outline]
        with service.projects._connection() as db:
            rows=db.execute('SELECT payload_json FROM section_review_receipts WHERE task_id=? ORDER BY sequence',(task['id'],)).fetchall()
        assert len(rows)==2 and 'private SQL' not in '\n'.join(r[0] for r in rows)
        monkeypatch.setattr(service.learning,'save_from_task',publish)
        assert service.review_held(record,task['id'],judge())['publication_performed']
        assert adapter.calls==4
        with service.projects._connection() as db:assert db.execute('SELECT count(*) FROM section_review_receipts WHERE task_id=?',(task['id'],)).fetchone()[0]==3


def test_concurrent_duplicate_review_does_not_invoke_second_reviewer(tmp_path,repository,git_exe):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,*_):
        entered,release=Event(),Event();results=[]
        def inspect(view):entered.set();assert release.wait(5);return assessment(view)
        thread=Thread(target=lambda:results.append(service.review_held(record,task['id'],judge(inspect))));thread.start()
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError,match='review_not_live'):service.review_held(record,task['id'],judge(lambda _:pytest.fail('duplicate reviewer')))
        finally:release.set();thread.join(5)
        assert len(results)==1 and results[0]['publication_performed'] and adapter.calls==4


def test_json_receipt_wrong_contract_and_tampered_evidence_cannot_release(tmp_path,repository,git_exe):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,*_):
        collector,candidate,_=service._held_live[task['id']]
        checked=assess(candidate,collector,service.review_contract,judge(),'scripted')
        with pytest.raises(ValueError):service.independent_reviews.finish(service.history,service.learning,candidate,checked.view())
        foreign=ReviewStore(service.projects,contract=replace(service.review_contract,required=('Different fixture.',)))
        with pytest.raises(ValueError):foreign.finish(service.history,service.learning,candidate,checked)
        with service.projects._connection() as db:
            import json
            payload=json.loads(db.execute('SELECT payload_json FROM agent_tasks WHERE id=?',(task['id'],)).fetchone()[0])
            payload['evidence'][0]['sha256']='0'*64
            db.execute('UPDATE agent_tasks SET payload_json=? WHERE id=?',(json.dumps(payload),task['id']))
        with pytest.raises(ValueError):service.review_held(record,task['id'],judge())
        assert client.get(route+'/learning').json()==[outline] and adapter.calls==4


def test_review_receipt_capacity_stops_before_inspection_and_http_cannot_release(tmp_path,repository,git_exe):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,*_):
        for _ in range(8):
            with pytest.raises(ValueError):service.review_held(record,task['id'],judge(lambda _:{}))
        with pytest.raises(ValueError,match='review_capacity'):
            service.review_held(record,task['id'],judge(lambda _:pytest.fail('capacity exceeded')))
        assert client.post(route+'/tasks/'+task['id']+'/approve',json={}).status_code==404
        assert client.get(route+'/learning').json()==[outline] and adapter.calls==4


def test_review_timeout_keeps_original_held_and_no_model_is_called_again(tmp_path,repository,git_exe):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,*_):
        clock=[0.0];service._review_clock=lambda:clock[0]
        def inspect(view):clock[0]=30.0;return assessment(view)
        with pytest.raises(ValueError,match='review_timeout'):service.review_held(record,task['id'],judge(inspect))
        archived=client.get(route+'/tasks/'+task['id']).json()
        assert archived['status']=='needs_review' and archived['result']==task['result']
        assert client.get(route+'/learning').json()==[outline] and adapter.calls==4


def test_restart_archives_cannot_recreate_live_release_authority(tmp_path,repository,git_exe):
    root,_,commit,transport,policy=fixture_policy(repository,git_exe);data=tmp_path/'agent';spec=contract(policy)
    with TestClient(create_app(data,github_transport=transport,git_executable=git_exe,model_adapter=ScriptedAdapter(actions('sink 是已读调用。')),
        section_review_policy=policy,review_contract=spec),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        task=terminal(client,route,task['id']);service._thread.join(5)
    with TestClient(create_app(data,github_transport=transport,git_executable=git_exe,
        section_review_policy=policy,review_contract=spec),base_url='http://127.0.0.1') as reopened:
        assert reopened.post(route+'/source/verify').json()['status']=='verified'
        service=reopened.app.state.task_service;record=service.projects.get(task['project_id'])
        with pytest.raises(ValueError,match='review_not_live'):service.review_held(record,task['id'],judge(lambda _:pytest.fail('JSON authority')))
        assert len(reopened.get(route+'/learning').json())==1 and not service._held_live


def test_live_review_capacity_blocks_a_new_task_before_model_or_storage(tmp_path,repository,git_exe):
    with held(tmp_path,repository,git_exe) as (client,service,record,route,task,outline,adapter,*_):
        item=service._held_live[task['id']]
        for i in range(7):service._held_live[f'fixture-other-{i}']=item
        before=client.get(route+'/tasks').json()
        result=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json()
        assert result['status']=='review_capacity' and adapter.calls==4
        assert client.get(route+'/tasks').json()==before
