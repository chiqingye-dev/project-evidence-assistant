"""Native plan actions and original billing path, exclusively mock HTTP."""
from dataclasses import replace
import json
import sqlite3
import httpx
import pytest

from project_evidence import section_native_contract as native
from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import AgentBudget, Usage, ScriptedAdapter
from project_evidence.deepseek_adapter import DeepSeekAdapter
from project_evidence.section_review import AcceptedReads
from project_evidence.section_semantic_review import ReviewContract
from project_evidence.generation_loop import GenerationSession
from project_evidence.source_ranges import RangedSourceTools
from test_source_coverage_binding import collected, PATH, TEXT
from test_verified_read_observer import Source
from test_section_native_contract import context, call, response as wire_response
from test_section_named_adapter import response
from test_deepseek_adapter import setup, KEY


def staged(stage):
    value=context().model_dump()
    value['history']=[dict(host_generation_coverage=dict(format='public_section_generation_v1',stage=stage))]
    return type(context()).model_validate(value)


@pytest.mark.parametrize('stage,allowed,forbidden',[
    ('read','read_excerpt','publish_section'),
    ('planning','submit_coverage_plan','publish_section'),
    ('prose','publish_section','read_excerpt'),
])
def test_native_stage_schema_and_decoder_refuse_actions_from_another_stage(stage,allowed,forbidden):
    ctx=staged(stage);names={f['function']['name'] for f in native.functions(ctx,{PATH:(1,5)})}
    assert allowed in names and forbidden not in names
    with pytest.raises(native.Rejected):native.decode(wire_response(call(forbidden,{})),ctx,{PATH:(1,5)},Usage())


def test_native_plan_and_final_claims_are_not_source_tools_or_part_of_body():
    draft=dict(criteria=[dict(number=1,targets=['f:0'],gap='')])
    ctx=staged('planning')
    turn=native.decode(wire_response(call('submit_coverage_plan',draft)),ctx,{PATH:(1,5)},Usage())
    assert turn.kind=='section_plan' and not turn.tool_calls and turn.final_output is None
    with pytest.raises(native.Rejected):native.decode(wire_response(call('submit_coverage_plan',draft,'one'),call('read_excerpt',dict(path=PATH,start_line=1,end_line=5),'two')),ctx,{PATH:(1,5)},Usage())
    args=dict(title=context().anchor.section_title,blocks=[dict(category='mechanism',heading='示例',text='静态声明。',support='fact',evidence_ids=['e1'])],gaps=[],coverage_claims=[])
    final=native.decode(wire_response(call('publish_section',args)),staged('prose'),{PATH:(1,5)},Usage())
    assert final.kind=='final' and 'coverage_claims' not in final.final_output.model_dump()
    assert 'submit_coverage_plan' not in {f['function']['name'] for f in native.functions(context(),{PATH:(1,5)})}


@pytest.mark.parametrize('overflow',[False,True])
def test_mock_native_read_plan_final_share_reservations_tokens_and_pre_dispatch_context_guard(tmp_path,monkeypatch,overflow):
    template,_,_=collected();collector=AcceptedReads(template.scope)
    spec=ReviewContract('a'*64,'FIXTURE',collector.scope.public_url,collector.scope.commit,('PRIVATE_EXPECTED_ANSWER',))
    session=GenerationSession(collector,('writes',),spec,('解释实际来源中的状态写入。',))
    tools=RangedSourceTools(Source(TEXT.encode()),{PATH:(1,5)})
    db=sqlite3.connect(tmp_path/'feedback.sqlite3')
    db.execute('CREATE TABLE feedback(id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    def execute(name,args):
        assert name=='read_excerpt'
        value=collector.read(tools,args);saved=dict(value,id='e1')
        db.execute('INSERT INTO feedback VALUES (?,?)',('e1',json.dumps(saved,ensure_ascii=False)));db.commit()
        collector.saved(value,json.loads(db.execute('SELECT payload FROM feedback').fetchone()[0]))
        return dict(value,evidence_id='e1')
    events=[]
    def record(event):
        events.append(event);collector.accept(event)
    requests=[];text='状态字段写入 WAIT，运行效果未验证。'
    def respond(request):
        payload=json.loads(request.content);requests.append(payload)
        ctx=json.loads(payload['messages'][1]['content'])
        assert 'PRIVATE_EXPECTED_ANSWER' not in request.content.decode()
        if len(requests)==1:
            chosen=call('read_excerpt',dict(path=PATH,start_line=1,end_line=5))
        elif len(requests)==2:
            assert any(e['type']=='tool_feedback_accepted' for e in events)
            chosen=call('submit_coverage_plan',dict(criteria=[dict(number=1,targets=['f:0'],gap='')]))
        else:
            assert any(e['type']=='coverage_plan_accepted' for e in events)
            chosen=call('publish_section',dict(title=context().anchor.section_title,
                blocks=[dict(category='mechanism',heading='状态',text=text,support='fact',evidence_ids=['e1'])],gaps=[],
                coverage_claims=[dict(number=1,target='f:0',field='block:1:text',quote=text,kind='explanation')]))
        return httpx.Response(200,json=response(chosen))
    base,ledger=setup(tmp_path,respond);reservations=[];reserve=ledger.reserve
    def counted(*a,**k):reservations.append(1);return reserve(*a,**k)
    monkeypatch.setattr(ledger,'reserve',counted)
    if overflow:
        original=session.context
        def pressured(refs):
            value=original(refs)
            if session.plan is not None:value['pressure']='x'*24000
            return value
        monkeypatch.setattr(session,'context',pressured)
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,enable_submission=True,
        goal='section_explanation',topic='submission',section_protocol='named_functions',section_read_ranges={PATH:(1,5)}).for_task(collector.scope.task_id,goal='section_explanation',topic='submission')
    result=run_agent(adapter,execute,lambda:None,lambda:False,initial_context=context(),limits=AgentBudget(max_retries=0),record_event=record,section_generation=session)
    assert result.status==('budget_exhausted' if overflow else 'completed')
    assert len(requests)==len(reservations)==(2 if overflow else 3)
    assert [p['max_tokens'] for p in requests]==([4000,3000] if overflow else [4000,3000,2000])
    assert result.counters.output_tokens==(2000 if overflow else 3000)
    assert result.counters.retries==0 and ledger.summary('first-trial')['unresolved_requests']==0
    if not overflow:
        held=session.freeze(result.final_output.model_dump(),[r.model_dump() for r in result.evidence])
        assert not held.view()['generation_coverage_report']['structural_blockers']
        assert held.view()['output']==result.final_output.model_dump()
    db.close()


@pytest.mark.parametrize('limits',[dict(max_turns=7),dict(max_tool_calls=13),dict(max_seconds=31),dict(max_output_tokens=4001),dict(max_retries=1)])
def test_host_cannot_expand_original_shared_limits_for_plan(limits):
    c,_,_=collected();spec=ReviewContract('a'*64,'FIXTURE',c.scope.public_url,c.scope.commit,('Private',))
    session=GenerationSession(c,('writes',),spec,('Public objective',))
    adapter=ScriptedAdapter([])
    with pytest.raises(ValueError):run_agent(adapter,lambda *_:None,lambda:None,lambda:False,
        initial_context=context(),limits=AgentBudget(max_retries=0,**{k:v for k,v in limits.items() if k!='max_retries'}) if 'max_retries' not in limits else AgentBudget(**limits),section_generation=session)
    assert adapter.calls==0


def test_legacy_json_contract_does_not_advertise_plan_or_sidecar(tmp_path):
    base,_=setup(tmp_path,lambda _:pytest.fail('No HTTP request expected'))
    schema=base._contract(context())['turn']
    assert schema['properties']['kind']['enum']==['tools','final']
    assert 'section_plan' not in schema['properties'] and 'coverage_claims' not in schema['properties']
    assert 'CoverageDraft' not in schema['$defs'] and 'BodyClaim' not in schema['$defs']


@pytest.mark.parametrize('stage',['planning','prose'])
def test_compact_profile_keeps_original_minimum_prose_reserve(stage):
    names={f['function']['name'] for f in native.functions(staged(stage),{PATH:(1,5)},output_profile='compact_v1',max_output_tokens=2399)}
    assert names=={'report_gap','request_clarification'}
