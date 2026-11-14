from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from project_evidence import section_native_contract as native
from project_evidence.generation_wire import objective_plan_model
from project_evidence.agent_protocol import ModelContext,ScriptedAdapter,AgentBudget
from project_evidence.agent_engine import run_agent
from test_section_native_contract import context
from test_prepaid_generation import fixture,script

def ctx(count=2):
    data=context().model_dump()
    data['history']=[dict(host_generation_coverage=dict(format='public_section_generation_v1',stage='planning',
        layout='preloaded_groups_v1',public_objectives=[f'Public goal {i}' for i in range(1,count+1)],
        public_objective_numbers=list(range(1,count+1)),group_keys=['g:calls','g:branches','g:queries','g:snapshot_fields','g:writes','g:payload_response']))]
    return ModelContext.model_validate(data)

def recorded_structure_fixture():
    # Same safe structure as task12ded1c5, NOT its unavailable gap text/response.
    return dict(criteria=[dict(number=1,targets=['g:calls','g:branches'],gap=''),
        dict(number=2,targets=['g:queries','g:snapshot_fields'],gap=''),
        dict(number=3,targets=['g:writes','g:payload_response'],gap=''),
        dict(number=4,targets=['g:calls'],gap='x'*57)])

@pytest.mark.parametrize('count',[1,2,12])
def test_current_public_goals_determine_exact_request_count_and_number_range(count):
    functions=native.functions(ctx(count),{'src/Example.java':((4,4),)},output_profile='compact_v1',max_output_tokens=4000)
    plan=next(f['function'] for f in functions if f['function']['name']=='submit_coverage_plan')
    rows=plan['parameters']['properties']['criteria'];number=rows['items']['properties']['number']
    assert rows['minItems']==rows['maxItems']==count and number['enum']==list(range(1,count+1))
    assert number['minimum']==1 and number['maximum']==count
    assert f'exactly {count} criteria rows' in plan['description']
    valid=dict(criteria=[dict(number=i,targets=['g:calls'],gap='') for i in range(1,count+1)])
    assert objective_plan_model(True,count).model_validate(valid)

def test_recorded_four_for_two_shape_fails_the_model_used_to_generate_request_schema():
    with pytest.raises(ValidationError):objective_plan_model(True,2).model_validate(recorded_structure_fixture())
    valid=recorded_structure_fixture();valid['criteria']=valid['criteria'][:2]
    assert objective_plan_model(True,2).model_validate(valid)
    valid['criteria'][1]['number']=3
    with pytest.raises(ValidationError):objective_plan_model(True,2).model_validate(valid)

@pytest.mark.parametrize('case',['missing_goals','wrong_numbers','bool_number'])
def test_group_request_with_invalid_public_goal_identity_stops_before_provider(case):
    data=ctx().model_dump();control=data['history'][0]['host_generation_coverage']
    if case=='missing_goals':control.pop('public_objectives')
    if case=='wrong_numbers':control['public_objective_numbers']=[1,3]
    if case=='bool_number':control['public_objective_numbers']=[True,2]
    with pytest.raises(native.Rejected):native.functions(ModelContext.model_validate(data),{'src/Example.java':((4,4),)})

def test_numbering_is_public_data_and_misbehaving_provider_is_still_rejected(tmp_path):
    c,s,execute,record,events,db=fixture(tmp_path,objectives=('Public one','Public two'))
    steps=script();steps[0]['section_plan']=recorded_structure_fixture()
    result=run_agent(ScriptedAdapter(steps),execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='failed' and result.counters.model_calls==1
    assert any(e['details'].get('generation_check')==dict(step='plan_accept',reason='plan_criteria') for e in result.events)
    assert not any(e['type']=='coverage_plan_accepted' for e in result.events)
    db.close()

def test_generation_context_exposes_original_numbered_goals_without_private_answers(tmp_path):
    c,s,execute,record,events,db=fixture(tmp_path);steps=script();first=steps[0]
    def plan(context):
        control=next(h['host_generation_coverage'] for h in context.history if 'host_generation_coverage' in h)
        assert control['public_objective_numbers']==[1] and control['public_objectives']==['Explain current source data.']
        assert 'PRIVATE_EXPECTED_ANSWER' not in json.dumps(context.model_dump())
        return first
    steps[0]=plan
    result=run_agent(ScriptedAdapter(steps),execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='completed' and result.counters.model_calls==2
    assert s.capacity_receipt['bound']['plain_chars']+512<=24000
    db.close()
