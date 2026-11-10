"""Offline contract boundary tests; no supplier calls or semantic acceptance."""
from copy import deepcopy
import json
import socket

import pytest

from project_evidence.agent_protocol import ModelContext, ModelTurn, SECTION_GOAL
from project_evidence import section_native_contract as contract
from test_agent_engine import CONTEXT
from test_learning_core import explanation
from test_learning_engine import anchor


RANGES = {'src/Example.java': ((1, 20), (35, 50))}
USAGE = {'input_tokens': 100, 'output_tokens': 20, 'mode':'scripted'}


def context():
    return ModelContext(**CONTEXT, goal=SECTION_GOAL, anchor={**anchor(True), 'topic':'submission'})


def call(name, args, identifier='call-1'):
    return {'id':identifier, 'type':'function', 'function':{
        'name':name, 'arguments':args if isinstance(args,str) else json.dumps(args,ensure_ascii=False)}}


def response(*calls):
    return {'object':'chat.completion', 'model':'deepseek-flash', 'choices':[{
        'finish_reason':'tool_calls', 'message':{'role':'assistant', 'content':None,'tool_calls':list(calls)}}]}


def decode(value):
    return contract.decode(value, context(), RANGES, USAGE)


def publication():
    value=explanation('evidence-1')
    value.pop('kind'); value.pop('section_id')
    value['blocks'][0]['text']='已读片段中的输入。\n```mermaid\nflowchart LR\nA["输入"] --> B["检查"]\n```'
    return value


@pytest.mark.parametrize('name,args',[
    ('find_files',{'pattern':'*.java'}),
    ('search_text',{'query':'lockQuota'}),
    ('read_excerpt',{'path':'src/Example.java','start_line':35,'end_line':50}),
    ('report_gap',{'message':'证据不足。','gaps':['缺少实现。']}),
    ('request_clarification',{'question':'请选择要读的小节。','options':['入口','事务']}),
    ('publish_section',publication()),
])
def test_function_names_map_to_original_contract_without_model_action_tags(name,args):
    envelope=response(call(name,args)); saved=deepcopy(envelope)
    result=decode(envelope)
    assert isinstance(result,ModelTurn) and envelope==saved
    assert result.usage.input_tokens==100
    if name in contract.SOURCE_NAMES:
        assert result.kind=='tools' and result.tool_calls[0].name==name
        assert result.tool_calls[0].arguments.items()>=args.items()
    else:
        assert result.kind=='final'
        if name=='publish_section':
            assert result.final_output.topic=='submission' and result.final_output.section_id=='entry'
            assert result.final_output.blocks[0].text==args['blocks'][0]['text']
            assert result.final_output.gaps==args['gaps']


def test_three_source_calls_preserve_identifiers_and_do_not_execute(monkeypatch):
    monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('Network access'))
    result=decode(response(*(call('find_files',{},f'call-{i}') for i in range(3))))
    assert [c.id for c in result.tool_calls]==['call-0','call-1','call-2']


@pytest.mark.parametrize('calls,reason',[
    ([call('submit_action',{})],'function_unknown'),
    ([call('find_files',{}),call('report_gap',{'message':'缺少证据','gaps':['实现未读']},'call-2')],'mixed_actions'),
    ([call('report_gap',{'message':'缺少证据','gaps':['实现未读']}),call('report_gap',{'message':'缺少证据','gaps':['实现未读']},'call-2')],'mixed_actions'),
    ([call('find_files',{}),call('find_files',{})],'duplicate_id'),
    ([call('find_files',{},str(i)) for i in range(4)],'call_count'),
    ([], 'call_count'),
])
def test_closed_action_choices(calls,reason):
    with pytest.raises(contract.Rejected,match=f'^{reason}$'): decode(response(*calls))


@pytest.mark.parametrize('args,reason',[
    ('{} trailing','arguments_json'),
    ('[]','arguments_object'),
    ('{"query":"a","query":"b"}','duplicate_field'),
    ('{"query":"a","extra":{"x":1,"x":2}}','duplicate_field'),
    ('{"action":{"kind":"tools"}}','arguments_shape'),
    ('{"kind":"tools","query":"a"}','arguments_shape'),
    ('{"query":"a","usage":{}}','arguments_shape'),
    ('{}','arguments_shape'),
    ('{"query":"api_key = synthetic-not-a-real-key"}','native_action_invalid'),
    ('{"query":"a","path_glob":"../private.java"}','native_action_invalid'),
    ('['*2000+']'*2000,'arguments_object'),
    ('x'*14001,'arguments_json'),
], ids=['trailing','array','duplicate','nested-duplicate','wrapper','kind','usage','missing','secret','traversal','deep-array','size'])
def test_invalid_arguments_are_rejected_without_heuristic_repair_or_value_leaks(args,reason):
    with pytest.raises(contract.Rejected) as error: decode(response(call('search_text',args)))
    assert error.value.reason==reason
    assert args not in str(error.value)
    assert 'synthetic-not-a-real-key' not in str(error.value.diagnostics)


@pytest.mark.parametrize('args',[
    {'path':'src/Example.java','start_line':20,'end_line':35},
    {'path':'src/Other.java','start_line':1,'end_line':10},
    {'path':'src/Example.java','start_line':True,'end_line':10},
    {'path':'src/Example.java','start_line':10,'end_line':1},
])
def test_read_permission_remains_discrete_and_strict(args):
    with pytest.raises(contract.Rejected,match='^read_outside_range$'): decode(response(call('read_excerpt',args)))


@pytest.mark.parametrize('change,reason',[
    (lambda v:v.update(title='其他小节'),'arguments_shape'),
    (lambda v:v.update(topic='query'),'arguments_shape'),
    (lambda v:v.update(section_id='other'),'arguments_shape'),
    (lambda v:v['blocks'][0].update(evidence_ids=[]),'native_action_invalid'),
    (lambda v:v['blocks'][0].update(evidence_ids=['evidence-1','evidence-1']),'native_action_invalid'),
    (lambda v:v['blocks'][0].update(text='x'*1201),'native_action_invalid'),
    (lambda v:v.update(blocks=[]),'native_action_invalid'),
])
def test_final_anchor_and_original_content_guards(change,reason):
    args=publication(); change(args)
    with pytest.raises(contract.Rejected,match=f'^{reason}$'): decode(response(call('publish_section',args)))


@pytest.mark.parametrize('change,reason',[
    (lambda v:v.update(object='wrong'),'envelope'),
    (lambda v:v.update(model='unknown'),'envelope'),
    (lambda v:v['choices'][0].update(finish_reason='stop'),'envelope'),
    (lambda v:v['choices'][0].update(finish_reason='length'),'output_limit'),
    (lambda v:v['choices'][0]['message'].update(role='user'),'envelope'),
    (lambda v:v['choices'][0]['message'].update(content='extra text'),'envelope'),
    (lambda v:v['choices'][0]['message'].update(reasoning_content='extra text'),'envelope'),
    (lambda v:v['choices'][0]['message']['tool_calls'][0]['function'].update(extra='x'),'envelope'),
])
def test_envelope_fails_closed(change,reason):
    value=response(call('find_files',{})); change(value)
    with pytest.raises(contract.Rejected,match=f'^{reason}$'): decode(value)


def test_builder_advertises_exact_functions_binds_anchor_and_keeps_original_guidance(monkeypatch):
    monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('Network access'))
    ctx=context(); before=ctx.model_dump(); ranges=deepcopy(RANGES)
    request=contract.build_request(ctx,ranges)
    assert ctx.model_dump()==before and ranges==RANGES
    assert request['max_tokens']==4000 and request['tool_choice']=='required'
    assert request['thinking']=={'type':'disabled'}
    definitions={tool['function']['name']:tool['function']['parameters'] for tool in request['tools']}
    assert set(definitions)==set(contract.SOURCE_NAMES+contract.FINAL_NAMES)
    for definition in definitions.values():
        assert definition['additionalProperties'] is False
        assert 'kind' not in definition['properties']
        assert '$ref' not in json.dumps(definition)
    assert definitions['publish_section']['properties']['title']['const']=='入口与参数'
    assert set(definitions['publish_section']['required'])=={'title','blocks'}
    assert len(definitions['read_excerpt']['oneOf'])==2
    guide,digest=contract.load_guidance()
    assert len(digest)==64
    assert all(word in guide for word in ('callback_evidence_v1','helper_evidence_v1','参考回答','未读AccountService'))
    assert all(word not in guide for word in ('json_single_object_v1','final_output.blocks'))


@pytest.mark.parametrize('budget',[0,4001,True,2.5])
def test_no_budget_expansion(budget):
    with pytest.raises(contract.Rejected,match='^budget$'): contract.build_request(context(),RANGES,max_output_tokens=budget)


@pytest.mark.parametrize('problem',['missing','oversized','old_format_only'])
def test_guidance_missing_or_drift_is_rejected(tmp_path,problem):
    (tmp_path/'learning-guide.txt').write_text('输出封装 json_single_object_v1：旧封装。\n\n语义规则。',encoding='utf-8')
    if problem!='missing':
        (tmp_path/'submission-native-section.txt').write_text('x'*12001 if problem=='oversized' else 'json_single_object_v1',encoding='utf-8')
    with pytest.raises(contract.Rejected,match='^guidance$'): contract.load_guidance(tmp_path)


def test_wrong_goal_and_empty_ranges_are_rejected():
    wrong=ModelContext(**CONTEXT)
    with pytest.raises(contract.Rejected,match='^context$'): contract.functions(wrong,RANGES)
    with pytest.raises(contract.Rejected,match='^ranges$'): contract.functions(context(),{})
