"""Compact opt-in narrows generation, never source/criteria or paid budgets."""
from copy import deepcopy
import json
import httpx
import pytest

from project_evidence import section_native_contract as native
from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import SECTION_GOAL, ModelContext, AgentBudget
from project_evidence.deepseek_adapter import DeepSeekAdapter
from test_deepseek_adapter import KEY, setup
from test_section_native_contract import context, publication, call, response, RANGES, USAGE
from test_section_function_carrier import chosen_context


def compact_context(tokens=4000):
    value=context().model_dump();value['remaining']['output_tokens']=tokens
    return ModelContext.model_validate(value)


def configured(tmp_path, handler):
    base,ledger=setup(tmp_path,handler)
    model=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,
        enable_submission=True,goal=SECTION_GOAL,topic='submission',
        section_protocol='named_functions',section_read_ranges={'A.java':(2,3)},
        section_output_profile='compact_v1')
    return model.for_task('compact-offline',goal=SECTION_GOAL,topic='submission'),ledger


def test_profile_narrows_only_publication_shape_and_preserves_read_permissions():
    original=native.functions(context(),RANGES)
    narrowed=native.functions(context(),RANGES,output_profile='compact_v1')
    assert [x['function']['name'] for x in narrowed]==[x['function']['name'] for x in original]
    for before,after in zip(original,narrowed):
        if before['function']['name']!='publish_section': assert before==after
    schema=next(x['function']['parameters'] for x in narrowed if x['function']['name']=='publish_section')
    assert schema['properties']['blocks']['maxItems']==6
    assert schema['properties']['blocks']['items']['properties']['text']['maxLength']==320
    assert schema['properties']['blocks']['items']['properties']['evidence_ids']['maxItems']==3
    assert set(schema['properties'])=={'title','blocks','gaps'}


@pytest.mark.parametrize('tokens',[1,1000,2399])
def test_low_remaining_allowance_removes_reads_and_publish_but_accepts_gap(tokens):
    ctx=compact_context(tokens)
    names=[x['function']['name'] for x in native.functions(ctx,RANGES,output_profile='compact_v1')]
    assert names==['report_gap','request_clarification']
    gap=response(call('report_gap',dict(message='余量不足完整说明。',gaps=['仍需证据。'])))
    result=native.decode(gap,ctx,RANGES,USAGE,output_profile='compact_v1')
    assert result.final_output.kind=='gap'
    for c in [call('read_excerpt',dict(path='src/Example.java',start_line=4,end_line=4)),call('publish_section',publication())]:
        with pytest.raises(native.Rejected,match='function_unknown'):
            native.decode(response(c),ctx,RANGES,USAGE,output_profile='compact_v1')


def test_threshold_and_actual_request_allowance_are_bound_not_raised():
    ctx=compact_context(3595);guidance=native.load_guidance()[0]
    request=native.request_body(ctx,RANGES,guidance,max_output_tokens=4000,output_profile='compact_v1')
    assert request['max_tokens']==3595 and '最多6块' in request['messages'][0]['content']
    assert request['tools']==native.functions(ctx,RANGES,output_profile='compact_v1',max_output_tokens=3595)
    low=native.request_body(ctx,RANGES,guidance,max_output_tokens=2399,output_profile='compact_v1')
    assert low['max_tokens']==2399 and len(low['tools'])==2
    assert len(native.functions(compact_context(2400),RANGES,output_profile='compact_v1'))==6


@pytest.mark.parametrize('change',[
    lambda x:x.update(blocks=[deepcopy(x['blocks'][0]) for _ in range(7)]),
    lambda x:x['blocks'][0].update(text='长'*321),
    lambda x:x['blocks'][0].update(heading='长'*49),
    lambda x:x['blocks'][0].update(evidence_ids=['a','b','c','d']),
    lambda x:x.update(gaps=['缺口']*4),
    lambda x:x.update(gaps=['缺'*101]),
],ids=['blocks','text','heading','references','gaps','gap_length'])
def test_upstream_non_strict_oversize_is_rejected_without_trimming(change):
    body=publication();change(body);before=deepcopy(body)
    value=response(call('publish_section',body))
    # All these shapes were within the original hard contract.
    assert native.decode(value,context(),RANGES,USAGE).kind=='final'
    with pytest.raises(native.Rejected,match='arguments_shape'):
        native.decode(value,context(),RANGES,USAGE,output_profile='compact_v1')
    assert body==before


def test_bounded_output_is_preserved_exactly_and_original_citations_remain_required():
    body=publication();value=response(call('publish_section',body))
    checked=native.decode(value,context(),RANGES,USAGE,output_profile='compact_v1')
    assert checked.final_output.blocks[0].text==body['blocks'][0]['text']
    assert checked.final_output.blocks[0].evidence_ids==body['blocks'][0]['evidence_ids']
    body['blocks'][0]['evidence_ids']=[]
    with pytest.raises(native.Rejected,match='native_action_invalid'):
        native.decode(response(call('publish_section',body)),context(),RANGES,USAGE,output_profile='compact_v1')


@pytest.mark.parametrize('length',[False,True],ids=['complete','provider_length'])
def test_actual_engine_shared_budget_and_fee_receipts_stay_original(tmp_path,length):
    budgets=[]
    body=publication()
    def handler(request):
        data=json.loads(request.content);budgets.append(data['max_tokens'])
        assert 'compact_v1' in data['messages'][0]['content']
        if len(budgets)==1: value=response(call('find_files',{}))
        elif len(budgets)==2:value=response(call('read_excerpt',dict(path='A.java',start_line=2,end_line=3)))
        else:value=response(call('publish_section',body))
        output=[243,162,3595 if length else 100][len(budgets)-1]
        value['usage']=dict(prompt_tokens=10,completion_tokens=output,total_tokens=10+output)
        if length and len(budgets)==3:value['choices'][0]['finish_reason']='length'
        return httpx.Response(200,json=value)
    adapter,ledger=configured(tmp_path,handler)
    def execute(name,args):
        if name=='find_files':return dict(status='ok',items=[])
        return dict(status='ok',evidence_id='evidence-1',path='A.java',start_line=2,end_line=3,
            sha256='a'*64,blob_oid='b'*40,public_url=chosen_context().public_url,
            commit=chosen_context().commit,text='synthetic source',truncated=False)
    result=run_agent(adapter,execute,lambda:None,lambda:False,
        initial_context=chosen_context(),limits=AgentBudget(max_retries=0))
    assert budgets==[4000,3757,3595]
    assert result.counters.retries==0 and result.counters.output_tokens==(4000 if length else 505)
    assert result.status==('budget_exhausted' if length else 'completed')
    if length:assert result.final_output is None
    assert ledger.summary('first-trial')['unresolved_requests']==0
    assert adapter.section_output_profile=='compact_v1'


@pytest.mark.parametrize('profile,protocol',[('unknown','named_functions'),(True,'named_functions'),('compact_v1','json_object'),('compact_v1','function_call')])
def test_invalid_profile_is_blocked_before_any_request_or_reservation(tmp_path,profile,protocol):
    base,ledger=setup(tmp_path,lambda _:pytest.fail('Unauthorized dispatch'))
    with pytest.raises(ValueError):DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,
        enable_submission=True,goal=SECTION_GOAL,topic='submission',section_protocol=protocol,
        section_read_ranges={'A.java':(2,3)},section_output_profile=profile)
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0
