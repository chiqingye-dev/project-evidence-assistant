from copy import deepcopy
import json
import pytest
from project_evidence import generation_loop as loop
from project_evidence.agent_protocol import AgentBudget,ScriptedAdapter
from project_evidence.agent_engine import run_agent
from project_evidence.generation_diagnostics import (claim_structure,safe_claim_structure,
    freeze_check,GenerationRejected,digest,encoded)
from project_evidence.task_diagnostics import engine_metadata
from test_prepaid_generation import fixture,script
from test_section_native_contract import context
from test_task_storage import fixture_store

SECRET='synthetic-secret-not-for-logs'


def data():
    plan=dict(public_objectives=['Public objective'],plan_digest='a'*64,
        targets={'g:writes':['e1']},criteria=[dict(number=1,targets=['g:writes'],gap='')])
    output=dict(title='Public title',blocks=[dict(heading='Heading',text='A static declaration.',evidence_ids=['e1'])],gaps=[])
    claims=[dict(number=1,target='g:writes',field='block:1:text',quote='static declaration',kind='explanation')]
    return claims,plan,output


def run(tmp_path,steps=None,record_override=None):
    c,s,execute,record,events,db=fixture(tmp_path)
    result=run_agent(ScriptedAdapter(steps or script()),execute,lambda:None,lambda:False,
        AgentBudget(max_retries=0),record_override(record) if record_override else record,context(),section_generation=s)
    return s,result,db


@pytest.mark.parametrize('case,reason',[
    ('target','body_claim_target'),('field','body_claim_field'),('quote','body_claim_quote'),
    ('overlap','body_claim_quote'),('duplicate','body_claim_target')])
def test_bad_provider_claims_have_safe_observation_then_specific_freeze_rejection(tmp_path,case,reason):
    steps=script();row=steps[-1]['coverage_claims'][0]
    if case=='target':row['target']=SECRET
    if case=='field':row['field']=SECRET
    if case=='quote':row['quote']=SECRET
    if case=='overlap':steps[-1]['final_output']['blocks'][0]['text']='aaaa';row['quote']='aa'
    if case=='duplicate':steps[-1]['coverage_claims'].append(deepcopy(row))
    s,result,db=run(tmp_path,steps)
    assert result.status=='completed' and result.counters.model_calls==2
    event=next(e for e in result.events if e['type']=='coverage_claims_observed')
    safe=engine_metadata(event)['public_claim_structure']
    assert safe_claim_structure(safe)==safe and SECRET not in json.dumps(safe)
    assert result.events.index(event)<next(i for i,e in enumerate(result.events) if e['type']=='finished')
    with pytest.raises(GenerationRejected) as caught:s.freeze(result.final_output.model_dump(),[e.model_dump() for e in result.evidence])
    assert freeze_check(s.freeze_step,caught.value)==dict(step='freeze_report',reason=reason)
    row=safe['rows'][-1]
    if case=='target':assert row['target_kind']=='unknown' and row['target'] is None
    if case=='field':assert row['field'] is None
    if case=='quote':assert row['quote_matches']==0
    if case=='overlap':assert row['quote_matches']==2
    if case=='duplicate':assert row['duplicate']
    db.close()


def test_same_body_with_valid_claims_is_unchanged_and_needs_independent_review(tmp_path):
    s,result,db=run(tmp_path)
    body=result.final_output.model_dump();held=s.freeze(body,[e.model_dump() for e in result.evidence]).view()
    assert held['output']==body and not held['generation_coverage_report']['structural_blockers']
    assert not held['publication_approval'] and not held['semantic_verified']
    observed=next(e for e in result.events if e['type']=='coverage_claims_observed')
    assert engine_metadata(observed)['public_claim_structure']['output_digest']==held['output_digest']
    db.close()


@pytest.mark.parametrize('phase,exception,reason',[
    ('complete',TypeError,'type_error'),('final_report',KeyError,'key_error'),('encoded',ValueError,'value_error')])
def test_freeze_substage_survives_without_exception_text(tmp_path,monkeypatch,phase,exception,reason):
    s,result,db=run(tmp_path)
    class PrivateError(exception):
        def __str__(self):pytest.fail('Exception text must not be read')
    def reject(*a,**k):raise PrivateError(SECRET)
    monkeypatch.setattr(loop,phase,reject)
    with pytest.raises(PrivateError) as caught:s.freeze(result.final_output.model_dump(),[e.model_dump() for e in result.evidence])
    expected=dict(complete='freeze_complete',final_report='freeze_report',encoded='freeze_encode')[phase]
    assert freeze_check(s.freeze_step,caught.value)==dict(step=expected,reason=reason)
    db.close()


def test_structure_does_not_hash_unknown_target_field_or_quote_text():
    claims,plan,body=data();row=claims[0];row.update(target=SECRET,field=SECRET,quote=SECRET)
    first=claim_structure(claims,plan,body)
    row.update(target='another-unread-reference',field='another-unread-field',quote='x'*len(SECRET))
    assert claim_structure(claims,plan,body)==first and SECRET not in encoded(first)


def test_maximum_claims_are_bounded_and_first_late_bad_row_is_visible():
    claims,plan,body=data();targets=[f'f:{i}' for i in range(256)]
    plan['targets']={t:['e1'] for t in targets};plan['criteria'][0]['targets']=targets
    claims=[dict(claims[0],target=t) for t in targets];claims[-1]['field']=SECRET
    view=claim_structure(claims,plan,body)
    assert view['claim_count']==256 and len(view['rows'])==12 and view['rows'][-1]['ordinal']==256
    assert view['reference_sample_truncated'] and view['counts']['unknown_fields']==1
    assert len(encoded(view).encode())<=4096 and safe_claim_structure(view)==view and SECRET not in encoded(view)
    with pytest.raises(GenerationRejected):claim_structure(claims+[claims[0]],plan,body)


@pytest.mark.parametrize('case',['valid','raw_quote','unknown_target','unknown_field','kind','bool_number','bool_ordinal','count','digest'])
def test_storage_revalidates_claim_metadata_even_with_resigned_tampering(tmp_path,case):
    projects,record,store=fixture_store(tmp_path);task=store.create(record,'scripted',{})
    claims,plan,body=data();view=claim_structure(claims,plan,body);row=view['rows'][0]
    if case=='raw_quote':row['quote']=SECRET
    if case=='unknown_target':row['target']=SECRET
    if case=='unknown_field':row['field']=SECRET
    if case=='kind':row['kind']=SECRET
    if case=='bool_number':row['number']=True
    if case=='bool_ordinal':row['ordinal']=True
    if case=='count':view['counts']['unknown_fields']=257
    view['structure_digest']=digest({k:v for k,v in view.items() if k!='structure_digest'})
    if case=='digest':view['structure_digest']='0'*64
    store.update(record.id,task['id'],event=dict(kind='coverage_claims_observed',message='fixed',public_claim_structure=view,raw_response=SECRET))
    saved=store.get(record.id,task['id'])['events'][-1]
    assert SECRET not in encoded(saved) and 'raw_response' not in saved
    assert ('public_claim_structure' in saved)==(case=='valid')


@pytest.mark.parametrize('mode',['return_false','raise'])
def test_missing_claim_snapshot_stops_after_current_return_without_retry(tmp_path,mode):
    def wrapper(record):
        def callback(event):
            if event['type']=='coverage_claims_observed':
                if mode=='return_false':return False
                raise RuntimeError(SECRET)
            return record(event)
        return callback
    s,result,db=run(tmp_path,record_override=wrapper)
    assert result.status=='failed' and result.counters.model_calls==2 and result.counters.retries==0
    assert any(engine_metadata(e).get('generation_check')==dict(step='event_persist',reason='event_not_saved') for e in result.events)
    assert SECRET not in encoded(result.model_dump())
    db.close()
