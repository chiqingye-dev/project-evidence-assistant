from copy import deepcopy
import pytest
from project_evidence import section_native_contract as native
from project_evidence.agent_protocol import ModelContext,ScriptedAdapter,AgentBudget
from project_evidence.agent_engine import run_agent
from project_evidence.generation_diagnostics import GenerationRejected
from test_objective_plan_schema import ctx
from test_prepaid_generation import fixture,script
from test_section_native_contract import context


def prose(count=2,gap=True,all_gap=False):
    value=ctx(count).model_dump();control=value['history'][0]['host_generation_coverage']
    control.update(stage='prose',plan_digest='a'*64,criteria=[dict(number=i,targets=[] if all_gap else ['g:writes'],gap='Unknown' if gap or all_gap else '') for i in range(1,count+1)])
    return value


def claim_schema(value):
    tools=native.functions(ModelContext.model_validate(value),{'src/Example.java':((4,4),)},output_profile='compact_v1',max_output_tokens=4000)
    return next(t['function'] for t in tools if t['function']['name']=='publish_section')


@pytest.mark.parametrize('count,gap,all_gap',[(1,False,False),(2,True,False),(12,False,False),(2,True,True)])
def test_body_request_uses_only_actual_public_numbers_and_assigned_references(count,gap,all_gap):
    value=prose(count,gap,all_gap);fn=claim_schema(value);row=fn['parameters']['properties']['coverage_claims']['items']['properties']
    assert row['number']['maximum']==count and row['number']['enum']==list(range(1,count+1))
    expected=([] if all_gap else ['g:writes'])+([None] if gap or all_gap else [])
    assert row['target']['enum']==expected
    assert 'g:queries' not in row['target']['enum'] # Present in source catalog, absent from bound plan.
    assert 'exactly once' in fn['description']
    assert fn['strict'] is False


@pytest.mark.parametrize('case',['missing','duplicate_number','bool_number','foreign','duplicate_target','unplanned_gap','bad_keys'])
def test_unbound_or_changed_group_plan_stops_request_construction(case):
    value=prose();control=value['history'][0]['host_generation_coverage']
    if case=='missing':control.pop('criteria')
    if case=='duplicate_number':control['criteria'][1]['number']=1
    if case=='bool_number':control['criteria'][0]['number']=True
    if case=='foreign':control['criteria'][0]['targets']=['g:foreign']
    if case=='duplicate_target':control['criteria'][0]['targets']*=2
    if case=='unplanned_gap':control['criteria'][0].update(targets=[],gap=' ')
    if case=='bad_keys':control['group_keys']=['g:foreign']
    with pytest.raises(native.Rejected):claim_schema(value)


def test_host_accepted_reversed_criterion_order_remains_supported():
    first=prose();second=deepcopy(first);second['history'][0]['host_generation_coverage']['criteria'].reverse()
    assert claim_schema(first)==claim_schema(second)


@pytest.mark.parametrize('case,reason',[('wrong_pair','body_claim_target'),('invented_quote','body_claim_quote')])
def test_optional_schema_never_weakens_local_pairing_or_unique_quote_checks(tmp_path,case,reason):
    c,s,execute,record,events,db=fixture(tmp_path,objectives=('Public one','Public two'))
    steps=script();steps[0]['section_plan']['criteria'].append(dict(number=2,targets=[],gap='Unknown'))
    claim=steps[-1]['coverage_claims'][0]
    if case=='wrong_pair':claim['number']=2 # Each scalar is allowed, this pair was never assigned.
    if case=='invented_quote':claim['quote']='A sentence that the body never wrote.'
    result=run_agent(ScriptedAdapter(steps),execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='completed' and result.counters.model_calls==2
    with pytest.raises(GenerationRejected) as caught:s.freeze(result.final_output.model_dump(),[e.model_dump() for e in result.evidence])
    assert caught.value.reason==reason
    db.close()
