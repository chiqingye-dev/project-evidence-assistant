from copy import deepcopy
import json
import pytest
from project_evidence.generation_diagnostics import plan_structure,safe_plan_structure,metadata,digest
from project_evidence.task_diagnostics import engine_metadata
from project_evidence.agent_protocol import ScriptedAdapter,AgentBudget
from project_evidence.agent_engine import run_agent
from project_evidence.generation_loop import GenerationSession
from test_prepaid_generation import fixture,script
from test_section_native_contract import context
from test_task_storage import fixture_store

SECRET='synthetic-secret-do-not-log'


@pytest.mark.parametrize('case,reason',[
    ('missing','plan_criteria'),('duplicate','plan_duplicate_target'),('foreign','plan_unknown_target'),
    ('gap','plan_missing_gap'),('shape','plan_shape'),('unexpected','unexpected_exception')])
def test_rejected_plan_keeps_finite_stage_action_and_redacted_structure(tmp_path,monkeypatch,case,reason):
    c,s,execute,record,events,db=fixture(tmp_path);steps=script();row=steps[0]['section_plan']['criteria'][0]
    if case=='missing':row['number']=2
    if case=='duplicate':row['targets']*=2
    if case=='foreign':row['targets']=[SECRET]
    if case=='gap':row['targets']=[]
    if case=='shape':row['gap']='x'*81
    if case=='unexpected':
        def fail(*a):raise RuntimeError(SECRET)
        monkeypatch.setattr(s,'accept',fail)
    adapter=ScriptedAdapter(steps)
    result=run_agent(adapter,execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='failed' and adapter.calls==1 and result.counters.retries==0
    returned=next(e for e in result.events if e['type']=='model_returned')
    assert engine_metadata(returned)['model_action_kind']=='section_plan'
    rejected=next(e for e in result.events if e['type']=='generation_rejected')
    assert engine_metadata(rejected)['generation_check']==dict(step='plan_accept',reason=reason)
    observed=next(e for e in result.events if e['type']=='coverage_plan_observed')
    assert engine_metadata(observed)['public_plan_structure']['identity_scope']=='safe_structure_only'
    assert SECRET not in json.dumps(result.model_dump())
    assert not any(e['type']=='coverage_plan_accepted' for e in result.events)
    db.close()


def test_accepted_plan_diagnostics_do_not_enter_model_context_or_approve_output(tmp_path):
    c,s,execute,record,events,db=fixture(tmp_path);steps=script();finish=steps[-1]
    def final(ctx):
        text=json.dumps(ctx.model_dump())
        assert all(k not in text for k in ('generation_check','public_plan_structure','model_action_kind'))
        return finish
    steps[-1]=final
    result=run_agent(ScriptedAdapter(steps),execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='completed' and result.counters.model_calls==2
    assert [e['details']['model_action_kind'] for e in result.events if e['type']=='model_returned']==['section_plan','final']
    held=s.freeze(result.final_output.model_dump(),[r.model_dump() for r in result.evidence]).view()
    assert not held['publication_approval'] and not held['semantic_verified']
    db.close()


def test_structure_never_hashes_gap_or_unknown_target_text_and_is_bounded():
    first=dict(criteria=[dict(number=1,targets=['g:writes',SECRET],gap=SECRET)])
    other=deepcopy(first);other['criteria'][0]['targets'][1]='another-unknown';other['criteria'][0]['gap']='x'*len(SECRET)
    a=plan_structure(first,1,['g:writes']);b=plan_structure(other,1,['g:writes'])
    assert a==b and SECRET not in json.dumps(a)
    worst=dict(criteria=[dict(number=i,targets=['g:snapshot_fields']*256,gap='x'*600) for i in range(1,13)])
    view=plan_structure(worst,12,['g:snapshot_fields'])
    assert safe_plan_structure(view)==view and len(json.dumps(view,separators=(',',':')).encode())<=4096


@pytest.mark.parametrize('case',['action','reason','raw_plan','gap_text','reference','digest','bool_number'])
def test_storage_revalidates_diagnostics_and_rejects_free_text(tmp_path,case):
    projects,record,store=fixture_store(tmp_path);task=store.create(record,'scripted',{})
    view=plan_structure(dict(criteria=[dict(number=1,targets=['g:writes'],gap='')]),1,['g:writes'])
    data=dict(kind='coverage_plan_observed',message='fixed',model_action_kind='section_plan',public_plan_structure=view,
        generation_check=dict(step='plan_accept',reason='plan_criteria'),raw_response=SECRET)
    if case=='action':data['model_action_kind']=SECRET
    if case=='reason':data['generation_check']['reason']=SECRET
    if case=='raw_plan':data['public_plan_structure']={'gap':SECRET}
    if case=='gap_text':view['rows'][0]['gap']=SECRET
    if case=='reference':view['rows'][0]['known_targets']=[SECRET]
    if case=='digest':view['structure_digest']='0'*64
    if case=='bool_number':view['rows'][0]['number']=True
    store.update(record.id,task['id'],event=data)
    saved=store.get(record.id,task['id'])['events'][-1]
    assert SECRET not in json.dumps(saved) and 'raw_response' not in saved
    if case=='action':assert 'model_action_kind' not in saved
    elif case=='reason':assert 'generation_check' not in saved
    else:assert 'public_plan_structure' not in saved


def test_refused_recorder_retains_in_memory_fixed_fallback_without_retry(tmp_path):
    c,s,execute,record,events,db=fixture(tmp_path);calls=[]
    def recorder(event):
        calls.append(event['type'])
        if event['type']=='coverage_plan_observed':return False
        return record(event)
    adapter=ScriptedAdapter(script())
    result=run_agent(adapter,execute,lambda:None,lambda:False,AgentBudget(max_retries=0),recorder,context(),section_generation=s)
    assert result.status=='failed' and adapter.calls==1 and calls.count('coverage_plan_observed')==1
    assert any(engine_metadata(e).get('generation_check')==dict(step='event_persist',reason='event_not_saved') for e in result.events)
    db.close()
