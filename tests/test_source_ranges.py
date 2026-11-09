"""Configured spans constrain every tool and model action without extra calls."""
import hashlib
import json
import httpx
import pytest
from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import ModelContext,SECTION_GOAL,OUTLINE_GOAL,GOAL
from project_evidence.deepseek_adapter import DeepSeekAdapter
from project_evidence.source_ranges import RangedSourceTools,normalize_read_ranges
from test_tools import FakeSource
from test_deepseek_adapter import setup,envelope,KEY
from test_learning_engine import anchor

@pytest.mark.parametrize('value',[
    [],True,{'A.java':(0,2)},{'A.java':(2,1)},{'A.java':(True,2)},
    {'A.java':(1,121)},{'A.java':(1,1_000_001)},{'A.java':(1,)},
    {'../A.java':(1,2)},{'D:/private/A.java':(1,2)},
    {'docs/M2-NEXT-CHAT-HANDOFF.md':(1,2)},{'.smtp.qq.local':(1,2)},
    {'*.java':(1,2)},{f'A{i}.java':(1,2) for i in range(17)},
])
def test_invalid_scopes_reject_without_reading_or_exporting_value(value):
    with pytest.raises(ValueError) as e:normalize_read_ranges(value)
    assert str(e.value)=='Read ranges must contain bounded permitted relative paths'

def test_all_tools_preserve_line_numbers_digest_and_hide_other_content():
    raw=b'outside-before\nvisible-one\nvisible-two\noutside-after\n'
    source=FakeSource({'A.java':raw,'Other.java':b'other-file'});tools=RangedSourceTools(source,{'A.java':(2,3)})
    assert [i['path'] for i in tools.find_files('*.java')['items']]==['A.java'] and not source.reads
    assert not tools.search_text('outside')['items']
    found=tools.search_text('visible-two')['items'];assert len(found)==1 and found[0]['line']==3
    result=tools.read_excerpt('A.java',2,3)
    assert result['text']=='visible-one\nvisible-two' and result['total_lines']==4
    assert (result['start_line'],result['end_line'])==(2,3) and result['sha256']==hashlib.sha256(raw).hexdigest()
    assert set(source.reads)=={'A.java'}

@pytest.mark.parametrize('path,start,end',[('A.java',1,3),('A.java',2,4),('Other.java',2,3),('A.java',True,3),([],2,3)])
def test_direct_out_of_range_reads_do_not_open_a_file(path,start,end):
    source=FakeSource({'A.java':b'a\nb\nc\nd'});tools=RangedSourceTools(source,{'A.java':(2,3)})
    assert tools.read_excerpt(path,start,end)['status']=='denied' and source.reads==[]

def test_whole_file_sensitive_content_guard_still_applies_outside_span():
    source=FakeSource({'A.java':b'password="synthetic-sensitive-value"\nvisible\n'})
    result=RangedSourceTools(source,{'A.java':(2,2)}).read_excerpt('A.java',2,2)
    assert result['status']=='denied' and 'synthetic-sensitive-value' not in json.dumps(result)

def make_adapter(tmp_path,respond,ranges):
    base,ledger=setup(tmp_path,respond)
    configured=DeepSeekAdapter(KEY,ledger,'first-trial',enable_query=True,transport=base._transport,section_read_ranges=ranges)
    return configured,ledger

def section_context():return ModelContext(commit='a'*40,public_url='https://github.com/example/public-java',goal=SECTION_GOAL,anchor=anchor(True))

def test_scope_reaches_real_request_and_task_clone_owns_immutable_configuration(tmp_path):
    ranges={'A.java':[2,3]};requests=[]
    def respond(request):requests.append(json.loads(request.content));return httpx.Response(200,json=envelope())
    base,ledger=make_adapter(tmp_path,respond,ranges);ranges['A.java'][0]=1;ranges['Other.java']=[1,80]
    adapter=base.for_task('range-clone',goal=SECTION_GOAL);ctx=section_context()
    contract=adapter._contract(ctx)['tool_arguments']['read_excerpt'];assert len(contract['oneOf'])==1
    item=contract['oneOf'][0];assert item['properties']['path']['const']=='A.java'
    assert item['properties']['start_line']['minimum']==2 and item['properties']['end_line']['maximum']==3
    assert set(item['required'])=={'path','start_line','end_line'} and item['additionalProperties'] is False
    adapter.generate(ctx,timeout_seconds=5,max_output_tokens=4000)
    assert len(requests)==1 and json.dumps(contract,ensure_ascii=False) in requests[0]['messages'][0]['content']
    assert ledger.summary('first-trial')['unresolved_requests']==0
    outline=base.for_task('outline',goal=OUTLINE_GOAL)
    octx=ModelContext(commit='a'*40,public_url='https://github.com/example/public-java',goal=OUTLINE_GOAL,anchor=anchor())
    assert 'oneOf' not in outline._contract(octx)['tool_arguments']['read_excerpt']
    assert 'oneOf' not in base._contract(ModelContext(commit='a'*40,public_url='https://github.com/example/public-java'))['tool_arguments']['read_excerpt']

@pytest.mark.parametrize('path,start,end',[('Other.java',2,3),('A.java',1,3),('A.java',2,4)])
def test_model_out_of_range_action_stops_before_tool_and_keeps_usage(tmp_path,path,start,end):
    content=json.dumps(dict(kind='tools',tool_calls=[dict(id='read',name='read_excerpt',arguments=dict(path=path,start_line=start,end_line=end))]))
    requests=[]
    def respond(request):requests.append(1);return httpx.Response(200,json=envelope(content))
    base,ledger=make_adapter(tmp_path,respond,{'A.java':(2,3)});adapter=base.for_task('range-violation',goal=SECTION_GOAL)
    result=run_agent(adapter,lambda *_:pytest.fail('Out-of-range call must not execute'),lambda:None,lambda:False,initial_context=section_context())
    assert result.status=='failed' and result.final_output is None and 'tool.read_outside_range' in result.model_dump_json()
    assert len(requests)==1 and ledger.summary('first-trial')['unresolved_requests']==0
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==28000

def test_in_range_model_action_is_preserved(tmp_path):
    content=json.dumps(dict(kind='tools',tool_calls=[dict(id='read',name='read_excerpt',arguments=dict(path='A.java',start_line=2,end_line=3))]))
    base,ledger=make_adapter(tmp_path,lambda _:httpx.Response(200,json=envelope(content)),{'A.java':(2,3)})
    turn=base.for_task('range-valid',goal=SECTION_GOAL).generate(section_context(),timeout_seconds=5,max_output_tokens=4000)
    assert turn.tool_calls[0].arguments==dict(path='A.java',start_line=2,end_line=3)
    assert ledger.summary('first-trial')['unresolved_requests']==0

@pytest.mark.parametrize('bounds',[
    [],[[1,2],[2,3]],[[8,9],[1,2]],[[1,121]],[[True,2]],
    [[1,2],3],[[1,2],[3]],[[1,2],[4,1]],
    [[2*i+1,2*i+1] for i in range(17)],
])
def test_invalid_discrete_spans_do_not_expand_or_expose_configuration(bounds):
    with pytest.raises(ValueError,match='^Read ranges must contain bounded permitted relative paths$'):
        normalize_read_ranges({'A.java':bounds})

def test_discrete_spans_mask_gap_preserve_numbers_and_deny_bridging_without_reads():
    raw=b'first\nsecond\ngap-private\ngap-more\nfifth\nsixth\n'
    source=FakeSource({'A.java':raw,'Other.java':b'foreign'})
    tools=RangedSourceTools(source,{'A.java':[(1,2),(5,6)]})
    assert not tools.search_text('gap')['items']
    assert tools.search_text('fifth')['items'][0]['line']==5
    assert tools.read_excerpt('A.java',5,6)['text']=='fifth\nsixth'
    assert tools.read_excerpt('A.java',1,2)['sha256']==hashlib.sha256(raw).hexdigest()
    assert tools.read_excerpt('A.java',5,6)['total_lines']==6
    before=list(source.reads)
    for start,end in [(2,5),(3,4),(1,6)]:assert tools.read_excerpt('A.java',start,end)['status']=='denied'
    assert source.reads==before

def test_discrete_scope_total_cap_and_whole_file_sensitive_guard():
    value={f'A{i}.java':(1,2) for i in range(15)};value['B.java']=[(1,2),(5,6)]
    with pytest.raises(ValueError):normalize_read_ranges(value)
    source=FakeSource({'A.java':b'first\npassword="synthetic-sensitive-value"\nlast\n'})
    result=RangedSourceTools(source,{'A.java':[(1,1),(3,3)]}).read_excerpt('A.java',3,3)
    assert result['status']=='denied' and 'synthetic-sensitive-value' not in json.dumps(result)

def test_discrete_schema_and_task_clone_copy_nested_configuration(tmp_path):
    ranges={'A.java':[[1,2],[5,6]]};requests=[]
    def respond(request):requests.append(json.loads(request.content));return httpx.Response(200,json=envelope())
    base,ledger=make_adapter(tmp_path,respond,ranges)
    ranges['A.java'][0][0]=0;ranges['A.java'].append([7,9])
    task=base.for_task('discrete-copy',goal=SECTION_GOAL)
    contract=task._contract(section_context())['tool_arguments']['read_excerpt']
    assert [(x['properties']['start_line']['minimum'],x['properties']['end_line']['maximum']) for x in contract['oneOf']]==[(1,2),(5,6)]
    assert all(x['properties']['path']['const']=='A.java' and x['additionalProperties'] is False for x in contract['oneOf'])
    task.generate(section_context(),timeout_seconds=5,max_output_tokens=4000)
    assert len(requests)==1 and json.dumps(contract,ensure_ascii=False) in requests[0]['messages'][0]['content']
    assert ledger.summary('first-trial')['unresolved_requests']==0

@pytest.mark.parametrize('start,end',[(3,4),(2,5),(1,6)])
def test_discrete_model_gap_or_bridge_denied_before_tool_and_usage_settled(tmp_path,start,end):
    content=json.dumps(dict(kind='tools',tool_calls=[dict(id='bridge',name='read_excerpt',arguments=dict(path='A.java',start_line=start,end_line=end))]))
    base,ledger=make_adapter(tmp_path,lambda _:httpx.Response(200,json=envelope(content)),{'A.java':[(1,2),(5,6)]})
    result=run_agent(base.for_task('discrete-denied',goal=SECTION_GOAL),lambda *_:pytest.fail('Gap must not execute'),lambda:None,lambda:False,initial_context=section_context())
    assert result.status=='failed' and 'tool.read_outside_range' in result.model_dump_json()
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==28000 and ledger.summary('first-trial')['unresolved_requests']==0
