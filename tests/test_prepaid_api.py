from dataclasses import replace
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter
from project_evidence.generation_loop import GenerationSession
from project_evidence.section_semantic_review import HostReviewer
from test_m0_api import repository,git_exe
from test_generation_loop import policy_and_script,TEXT
from test_section_review_api import prepare
from test_section_semantic_review import assessment
from test_agent_api import terminal


def setup(repository,git_exe):
    root,controller,commit,transport,policy,spec,script,_=policy_and_script(repository,git_exe)
    policy=replace(policy,generation_layout='preloaded_groups_v1')
    final=script[-1]
    def finish(ctx):
        value=final(ctx);value['coverage_claims'][0]['target']='g:branches';return value
    steps=script[:2]+[dict(kind='section_plan',section_plan=dict(criteria=[dict(number=1,targets=['g:branches'],gap='')])),finish]
    return root,commit,transport,policy,spec,steps


def test_actual_commits_precede_first_section_model_and_original_review_releases_once(tmp_path,repository,git_exe):
    root,commit,transport,policy,spec,steps=setup(repository,git_exe);first=steps[-2];box={}
    def plan(ctx):
        service=box['service'];task=service.history.get(service._active_project,service._active_task)
        assert task['counters']['model_calls']==1
        kinds=[e['kind'] for e in task['events']]
        assert kinds.index('tool_feedback_accepted')<kinds.index('generation_capacity_checked')<kinds.index('model_requested')
        with service.projects._connection() as db:assert db.execute('SELECT id FROM evidence WHERE id=?',(ctx.observations[0].result['evidence_id'],)).fetchone()
        assert all(o.call_id.startswith('host-read-') for o in ctx.observations)
        return first
    steps[-2]=plan;adapter=ScriptedAdapter(steps)
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy,review_contract=spec),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service;box['service']=service
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json();task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status']=='needs_review' and task['counters']['model_calls']==2 and adapter.calls==4
        held=service.held_reviews.get(task['project_id'],task['id']);original=deepcopy(task['result'])
        assert held['generation_capacity']['source_reads']==1 and not held['generation_capacity']['native_bytes_checked']
        assert held['generation_plan']['target_mode']=='groups' and not held['generation_coverage_report']['structural_blockers']
        receipt=service.review_held(service.projects.get(task['project_id']),task['id'],HostReviewer('fixture','scripted',assessment))
        assert receipt['publication_performed'] and not receipt['semantic_verified']
        assert service.history.get(task['project_id'],task['id'])['result']==original
        assert len(client.get(route+'/learning').json())==2


@pytest.mark.parametrize('case',['cancel','event','save','capacity'])
def test_actual_preload_failures_stop_before_first_section_dispatch(tmp_path,repository,git_exe,monkeypatch,case):
    root,commit,transport,policy,spec,steps=setup(repository,git_exe);adapter=ScriptedAdapter(steps);box={}
    if case in ('cancel','capacity'):
        original=GenerationSession.check_capacity
        def checked(self,*a,**k):
            if case=='cancel':
                service=box['service'];service.cancel(service.projects.get(service._active_project),service._active_task)
            if case=='capacity':
                from project_evidence.source_policy import SourceError
                raise SourceError('budget_exhausted','Synthetic fixed forecast overflow.')
            return original(self,*a,**k)
        monkeypatch.setattr(GenerationSession,'check_capacity',checked)
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter,section_review_policy=policy,review_contract=spec),base_url='http://127.0.0.1') as client:
        route,outline=prepare(client,root,commit);service=client.app.state.task_service;box['service']=service
        if case=='save':monkeypatch.setattr(service.projects,'save_evidence',lambda *_:(_ for _ in ()).throw(ValueError('Synthetic save failure')))
        if case=='event':
            update=service.history.update
            def reject(*a,**k):
                if k.get('event',{}).get('kind')=='tool_feedback_accepted':return False
                return update(*a,**k)
            monkeypatch.setattr(service.history,'update',reject)
        task=client.post(route+'/learning/section',json=dict(outline_id=outline['id'],section_id='entry')).json();task=terminal(client,route,task['id']);service._thread.join(5)
        assert task['status']==dict(cancel='cancelled',event='failed',save='budget_exhausted',capacity='budget_exhausted')[case]
        assert task['counters']['model_calls']==0 and adapter.calls==2 and not service._held_live
        assert len(client.get(route+'/learning').json())==1
