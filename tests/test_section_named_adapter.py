"""Opt-in adapter and original engine/ledger checks with mock HTTP only."""
from copy import deepcopy
import json

import httpx
import pytest

from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import ModelError, OUTLINE_GOAL, SECTION_GOAL, AgentBudget
from project_evidence.deepseek_adapter import DeepSeekAdapter, ENDPOINT
from project_evidence import section_native_contract as contract
from test_deepseek_adapter import KEY, setup
from test_section_function_carrier import chosen_context, native
from test_section_native_contract import call, publication


def response(*calls):
    value=native()
    value['choices'][0]['message']['tool_calls']=list(calls)
    return value


def configured(tmp_path,respond):
    base,ledger=setup(tmp_path,respond)
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,enable_submission=True,
        goal=SECTION_GOAL,topic='submission',section_protocol='named_functions',section_read_ranges={'A.java':(2,3)})
    return adapter.for_task('named-section-fixture',goal=SECTION_GOAL,topic='submission'),ledger


def test_two_turn_original_engine_tools_receipts_and_content_are_preserved(tmp_path):
    requests=[]; executed=[]; body=publication()
    def respond(request):
        assert str(request.url)==ENDPOINT and request.headers['authorization']=='Bearer '+KEY
        payload=json.loads(request.content); requests.append(payload)
        assert KEY.encode() not in request.content
        assert payload['tool_choice']=='required' and payload['thinking']=={'type':'disabled'}
        assert payload['stream'] is False and 'response_format' not in payload
        assert len(payload['tools'])==6 and all(t['function']['strict'] is False for t in payload['tools'])
        assert 'json_single_object_v1' not in payload['messages'][0]['content']
        assert '"turn"' not in payload['messages'][0]['content']
        ctx=json.loads(payload['messages'][1]['content'])
        return httpx.Response(200,json=response(call('publish_section',body) if ctx['observations'] else
            call('read_excerpt',{'path':'A.java','start_line':2,'end_line':3})))
    adapter,ledger=configured(tmp_path,respond)
    def execute(name,args):
        executed.append((name,args))
        return dict(status='ok',evidence_id='evidence-1',path='A.java',start_line=2,end_line=3,
            sha256='a'*64,blob_oid='b'*40,public_url=chosen_context().public_url,commit=chosen_context().commit,
            text='synthetic source',truncated=False)
    result=run_agent(adapter,execute,lambda:None,lambda:False,initial_context=chosen_context())
    assert result.status=='completed' and len(requests)==2 and len(executed)==1
    assert result.counters.model_calls==2 and result.counters.retries==0
    assert result.final_output.blocks[0].text==body['blocks'][0]['text']
    assert result.citations_validated and result.semantic_verified is False
    summary=ledger.summary('first-trial')
    assert summary['accounted_upper_bound_microyuan']==56000 and summary['unresolved_requests']==0


@pytest.mark.parametrize('case,reason',[
    ('wrapper','arguments_shape'),('duplicate','duplicate_field'),('unknown','function_unknown'),
    ('mixed','mixed_actions'),('outside','read_outside_range'),('extra','arguments_shape'),
    ('bad_argument','native_action_invalid'),('content','envelope'),('length','output_limit'),
])
def test_rejected_actions_settle_valid_receipts_once_and_never_execute_or_retry(tmp_path,case,reason):
    value=response(call('search_text',{'query':'sample'})); message=value['choices'][0]['message']
    fn=message['tool_calls'][0]['function']
    if case=='wrapper':fn['arguments']=json.dumps({'action':{'private':KEY}})
    if case=='duplicate':fn['arguments']='{"query":"first","query":"second"}'
    if case=='unknown':fn['name']='submit_action'
    if case=='mixed':message['tool_calls'].append(call('report_gap',{'message':'缺少证据','gaps':['未读实现']},'call-2'))
    if case=='outside':fn.update(name='read_excerpt',arguments=json.dumps({'path':'A.java','start_line':2,'end_line':4}))
    if case=='extra':fn['arguments']=json.dumps({'query':'sample','usage':{'private':KEY}})
    if case=='bad_argument':fn['arguments']=json.dumps({'query':'x','path_glob':'../private.java'})
    if case=='content':message['content']='private-'+KEY
    if case=='length':value['choices'][0]['finish_reason']='length'
    requests=[]
    adapter,ledger=configured(tmp_path,lambda _:requests.append(1) or httpx.Response(200,json=value))
    result=run_agent(adapter,lambda *_:pytest.fail('Invalid action executed'),lambda:None,lambda:False,initial_context=chosen_context())
    assert result.status==('budget_exhausted' if case=='length' else 'failed') and result.final_output is None
    assert len(requests)==1 and result.counters.tool_calls==result.counters.retries==0
    assert 'section_native.'+reason in result.model_dump_json()
    assert KEY not in result.model_dump_json() and 'private-' not in result.model_dump_json()
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==28000
    assert ledger.summary('first-trial')['unresolved_requests']==0


@pytest.mark.parametrize('case',['missing_total','wrong_total','negative','wrong_model','wrong_object'])
def test_untrusted_receipt_keeps_original_uncertain_hold(tmp_path,case):
    value=response(call('find_files',{}))
    if case=='missing_total':value['usage'].pop('total_tokens')
    if case=='wrong_total':value['usage']['total_tokens']=1
    if case=='negative':value['usage']['prompt_tokens']=-1
    if case=='wrong_model':value['model']='other'
    if case=='wrong_object':value['object']='response'
    adapter,ledger=configured(tmp_path,lambda _:httpx.Response(200,json=value))
    with pytest.raises(ModelError) as error: adapter.generate(chosen_context(),timeout_seconds=5,max_output_tokens=4000)
    assert error.value.code=='cost_uncertain' and ledger.summary('first-trial')['unresolved_requests']==1


@pytest.mark.parametrize('options',[
    {}, {'enable_query':True},{'enable_followup':True},{'topic':'query'}, {'goal':OUTLINE_GOAL},
    {'section_read_ranges':{}}, {'enable_consumption':True},{'enable_judging':True},
])
def test_named_functions_do_not_grant_other_purposes_or_unbounded_access(tmp_path,options):
    base,ledger=setup(tmp_path,lambda _:pytest.fail('Invalid configuration sent'))
    defaults=dict(enable_submission=True,goal=SECTION_GOAL,topic='submission',section_read_ranges={'A.java':(2,3)})
    if not options: defaults.pop('enable_submission')
    with pytest.raises(ValueError):DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,
        section_protocol='named_functions',**(defaults|options))
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0


def test_clone_isolation_and_task_frozen_guidance(tmp_path,monkeypatch):
    adapter,ledger=configured(tmp_path,lambda _:httpx.Response(200,json=response(call('report_gap',{'message':'缺证据','gaps':['未读实现']}))))
    assert adapter.allowed_goals=={SECTION_GOAL}
    with pytest.raises(ValueError): adapter.for_task('other',goal=OUTLINE_GOAL,topic='submission')
    frozen=adapter.guidance_sha256
    monkeypatch.setattr(contract,'load_guidance',lambda:pytest.fail('Mid-task prompt reload'))
    assert adapter.generate(chosen_context(),timeout_seconds=5,max_output_tokens=4000).final_output.kind=='gap'
    assert adapter.guidance_sha256==frozen


@pytest.mark.parametrize('budget',[0,4001,True])
def test_invalid_output_budget_never_reserves_or_dispatches(tmp_path,budget):
    adapter,ledger=configured(tmp_path,lambda _:pytest.fail('Budget-invalid send'))
    with pytest.raises(ModelError) as error:adapter.generate(chosen_context(),timeout_seconds=5,max_output_tokens=budget)
    assert error.value.request_sent is False and ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0


@pytest.mark.parametrize('case',['unread','output_limit','context_limit'])
def test_original_engine_citation_and_character_limits_still_reject(tmp_path,case):
    body=publication()
    adapter,ledger=configured(tmp_path,lambda _:httpx.Response(200,json=response(call('publish_section',body))))
    limits=AgentBudget(max_output_chars=10) if case=='output_limit' else AgentBudget(max_context_chars=10) if case=='context_limit' else None
    result=run_agent(adapter,lambda *_:pytest.fail('No source request expected'),lambda:None,lambda:False,
        initial_context=chosen_context(),limits=limits)
    assert result.status==('failed' if case=='unread' else 'budget_exhausted') and result.final_output is None
    assert ledger.summary('first-trial')['unresolved_requests']==0
