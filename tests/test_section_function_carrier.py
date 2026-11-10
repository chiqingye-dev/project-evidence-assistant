"""Opt-in non-Beta action carrier; strict local validation, fees and no retry."""
from copy import deepcopy
import json

import httpx
import pytest

from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import ModelContext, ModelError, OUTLINE_GOAL, SECTION_GOAL
from project_evidence.deepseek_adapter import DeepSeekAdapter, ENDPOINT
from test_agent_engine import CONTEXT
from test_deepseek_adapter import KEY, envelope, setup
from test_learning_engine import anchor


def chosen_context():
    chosen=anchor(True);chosen['topic']='submission'
    return ModelContext(**CONTEXT,goal=SECTION_GOAL,anchor=chosen)


def tools():
    return dict(kind='tools',tool_calls=[dict(id='read-one',name='read_excerpt',arguments=dict(path='A.java',start_line=2,end_line=3))])


def final():
    return dict(kind='final',final_output=dict(kind='section_explanation',topic='submission',section_id='entry',title='合成封装检查',
        blocks=[dict(category='processing',heading='合成结果',text='仅检查封装与状态，不是源码语义验收。',support='candidate',evidence_ids=[])],gaps=['未调用真实供应商。']))


def native(action=None):
    result=envelope()
    result['object']='chat.completion'
    result['usage']['total_tokens']=11000
    result['choices']=[dict(finish_reason='tool_calls',message=dict(role='assistant',content=None,
        tool_calls=[dict(id='carrier-one',type='function',function=dict(name='submit_action',arguments=json.dumps(action or tools(),ensure_ascii=False)))]))]
    return result


def configured(tmp_path,respond):
    base,ledger=setup(tmp_path,respond)
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,enable_submission=True,
        goal=SECTION_GOAL,topic='submission',section_protocol='function_call',section_read_ranges={'A.java':(2,3)}
        ).for_task('section-function-fixture',goal=SECTION_GOAL,topic='submission')
    return adapter,ledger


def test_forced_carrier_keeps_endpoint_original_action_and_exact_receipts(tmp_path):
    requests=[]
    def respond(request):
        assert str(request.url)==ENDPOINT and '/beta/' not in str(request.url)
        body=json.loads(request.content);requests.append(body)
        assert KEY.encode() not in request.content
        assert body['thinking']=={'type':'disabled'} and body['stream'] is False
        assert body['tool_choice']=={'type':'function','function':{'name':'submit_action'}}
        assert body['tools'][0]['function']['strict'] is False and 'response_format' not in body
        assert 'section_function_v1' in body['messages'][0]['content']
        assert 'usage' not in body['tools'][0]['function']['parameters']['properties']
        ctx=json.loads(body['messages'][1]['content']);assert ctx['goal']==SECTION_GOAL and ctx['anchor']['topic']=='submission'
        return httpx.Response(200,json=native(tools() if not ctx['observations'] else final()))
    adapter,ledger=configured(tmp_path,respond)
    executed=[]
    def execute(name,args):
        executed.append((name,args))
        return dict(status='ok',evidence_id='piece-one',path='A.java',start_line=2,end_line=3,
            sha256='a'*64,blob_oid='b'*40,public_url=CONTEXT['public_url'],commit=CONTEXT['commit'],
            text='synthetic source',truncated=False)
    result=run_agent(adapter,execute,lambda:None,lambda:False,initial_context=chosen_context())
    assert result.status=='completed' and len(requests)==2 and len(executed)==1
    assert executed[0][1]==dict(path='A.java',start_line=2,end_line=3)
    assert result.counters.retries==0 and ledger.summary('first-trial')['accounted_upper_bound_microyuan']==56000
    assert ledger.summary('first-trial')['unresolved_requests']==0


@pytest.mark.parametrize('case',['plain','mixed','multiple','wrong_name','finish','reasoning','no_id','wrong_type',
    'arguments_type','empty_arguments','extra_function_field','wrapper','suffix','usage','outside_range'])
def test_invalid_carrier_cannot_dispatch_tools_or_salvage_content(tmp_path,case):
    value=native();choice=value['choices'][0];message=choice['message'];call=message['tool_calls'][0];function=call['function']
    if case=='plain':choice['finish_reason']='stop';message.pop('tool_calls');message['content']=json.dumps(tools())
    if case=='mixed':message['content']='private-'+KEY
    if case=='multiple':message['tool_calls'].append(deepcopy(call))
    if case=='wrong_name':function['name']='read_excerpt'
    if case=='finish':choice['finish_reason']='length'
    if case=='reasoning':message['reasoning_content']='private-'+KEY
    if case=='no_id':call.pop('id')
    if case=='wrong_type':call['type']='custom'
    if case=='arguments_type':function['arguments']=tools()
    if case=='empty_arguments':function['arguments']=' '
    if case=='extra_function_field':function['private']=KEY
    if case=='wrapper':function['arguments']=json.dumps(dict(action=tools()))
    if case=='suffix':function['arguments']+=',private-'+KEY
    if case=='usage':function['arguments']=json.dumps(dict(tools(),usage=dict(private=KEY)))
    if case=='outside_range':
        action=tools();action['tool_calls'][0]['arguments']['end_line']=4;function['arguments']=json.dumps(action)
    requests=[]
    def respond(request):requests.append(1);return httpx.Response(200,json=value)
    adapter,ledger=configured(tmp_path,respond)
    result=run_agent(adapter,lambda *_:pytest.fail('Invalid carrier must not dispatch tools'),lambda:None,lambda:False,initial_context=chosen_context())
    assert result.status==('budget_exhausted' if case=='finish' else 'failed') and result.final_output is None and len(requests)==1
    assert result.counters.tool_calls==result.counters.retries==0
    assert KEY not in result.model_dump_json() and 'private-' not in result.model_dump_json()
    if case=='suffix':assert 'json.suffix_comma' in result.model_dump_json()
    if case=='outside_range':assert 'tool.read_outside_range' in result.model_dump_json()
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==28000
    assert ledger.summary('first-trial')['unresolved_requests']==0


@pytest.mark.parametrize('options',[dict(section_protocol=True),dict(section_protocol='unknown'),
    dict(section_protocol='function_call'),
    dict(section_protocol='function_call',goal=SECTION_GOAL,topic='query',enable_submission=True),
    dict(section_protocol='function_call',goal=SECTION_GOAL,topic='submission',enable_submission=True,enable_followup=True),
    dict(section_protocol='function_call',goal=SECTION_GOAL,topic='submission',enable_submission=True,enable_query=True)])
def test_carrier_is_not_an_implicit_permission_or_topic_expansion(tmp_path,options):
    base,ledger=setup(tmp_path,lambda _:pytest.fail('Invalid configuration must not send'))
    with pytest.raises(ValueError):DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,**options)
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0


def test_task_clone_keeps_protocol_and_refuses_other_goals_before_billing(tmp_path):
    adapter,ledger=configured(tmp_path,lambda _:pytest.fail('Other goals must not send'))
    assert adapter.section_protocol=='function_call' and adapter.allowed_goals=={SECTION_GOAL}
    with pytest.raises(ValueError):adapter.for_task('outline',goal=OUTLINE_GOAL,topic='submission')
    context=chosen_context();context=context.model_copy(update={'anchor':context.anchor.model_copy(update={'topic':'query'})})
    with pytest.raises(ModelError) as error:adapter.generate(context,timeout_seconds=5,max_output_tokens=4000)
    assert error.value.request_sent is False and ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0


def test_default_section_protocol_still_sends_original_json_content_request(tmp_path):
    requests=[]
    def respond(request):
        body=json.loads(request.content);requests.append(body)
        assert str(request.url)==ENDPOINT and body['response_format']=={'type':'json_object'} and 'tools' not in body
        return httpx.Response(200,json=envelope(json.dumps(final(),ensure_ascii=False)))
    base,ledger=setup(tmp_path,respond)
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,enable_submission=True).for_task('default-section',goal=SECTION_GOAL,topic='submission')
    assert adapter.generate(chosen_context(),timeout_seconds=5,max_output_tokens=4000).kind=='final' and len(requests)==1


@pytest.mark.parametrize('case',['missing_total','wrong_total'])
def test_invalid_provider_receipt_stays_uncertain_without_dispatching_tools(tmp_path,case):
    value=native()
    if case=='missing_total':value['usage'].pop('total_tokens')
    else:value['usage']['total_tokens']=1
    requests=[]
    adapter,ledger=configured(tmp_path,lambda _:requests.append(1) or httpx.Response(200,json=value))
    with pytest.raises(ModelError) as error:adapter.generate(chosen_context(),timeout_seconds=5,max_output_tokens=4000)
    assert error.value.code=='cost_uncertain' and len(requests)==1
    assert ledger.summary('first-trial')['unresolved_requests']==1
    with ledger.store._connection() as c:
        row=dict(c.execute('SELECT * FROM model_spend').fetchone())
        assert row['state']=='uncertain' and row['charged'] is None
