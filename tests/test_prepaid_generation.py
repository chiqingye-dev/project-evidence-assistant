from copy import deepcopy
import json,sqlite3
import pytest
from project_evidence.section_review import AcceptedReads
from project_evidence.section_semantic_review import ReviewContract
from project_evidence.generation_loop import GenerationSession
from project_evidence.agent_protocol import ScriptedAdapter, AgentBudget, ModelError
from project_evidence.agent_engine import run_agent
from project_evidence.source_ranges import RangedSourceTools
from project_evidence.source_policy import SourceError
from project_evidence import section_native_contract as native
from test_source_coverage_binding import collected, PATH, TEXT
from test_verified_read_observer import Source
from test_section_native_contract import context


def fixture(tmp_path,objectives=('Explain current source data.',)):
    template,_,_=collected();c=AcceptedReads(template.scope)
    contract=ReviewContract('a'*64,'FIXTURE',c.scope.public_url,c.scope.commit,('PRIVATE_EXPECTED_ANSWER',))
    session=GenerationSession(c,('writes',),contract,objectives,layout='preloaded_groups_v1')
    tools=RangedSourceTools(Source(TEXT.encode()),{PATH:(1,5)})
    db=sqlite3.connect(tmp_path/'feedback.sqlite3');db.execute('CREATE TABLE feedback(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    events=[]
    def execute(name,args):
        assert name=='read_excerpt'
        result=c.read(tools,args);saved=dict(result,id='e1')
        db.execute('INSERT INTO feedback VALUES (?,?)',('e1',json.dumps(saved,ensure_ascii=False)));db.commit()
        c.saved(result,json.loads(db.execute('SELECT payload FROM feedback').fetchone()[0]))
        return dict(result,evidence_id='e1')
    def record(event):events.append(deepcopy(event));c.accept(event)
    return c,session,execute,record,events,db


def script():
    text='源数据仅表示静态声明，仍需独立语义审查。'
    return [dict(kind='section_plan',section_plan=dict(criteria=[dict(number=1,targets=['g:writes'],gap='')])),
        dict(kind='final',final_output=dict(kind='section_explanation',topic='submission',section_id='entry',title='Fixture',
            blocks=[dict(category='mechanism',heading='Data',text=text,support='candidate',evidence_ids=['e1'])],gaps=[]),
            coverage_claims=[dict(number=1,target='g:writes',field='block:1:text',quote=text,kind='explanation')])]


def test_committed_preload_and_worst_case_gate_precede_first_model(tmp_path):
    c,s,execute,record,events,db=fixture(tmp_path)
    steps=script();first=steps[0];frames=[]
    def plan(ctx):
        assert len(ctx.observations)==1 and ctx.observations[0].call_id=='host-read-1'
        assert 'PRIVATE_EXPECTED_ANSWER' not in json.dumps(ctx.model_dump())
        assert [e['type'] for e in events].index('generation_capacity_checked')<[e['type'] for e in events].index('model_requested')
        assert db.execute('SELECT count(*) FROM feedback').fetchone()[0]==1
        names={f['function']['name'] for f in native.functions(ctx,{PATH:(1,5)})}
        assert names=={'submit_coverage_plan','report_gap','request_clarification'}
        frames.append(ctx.model_dump());return first
    steps[0]=plan;adapter=ScriptedAdapter(steps)
    result=run_agent(adapter,execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='completed' and result.counters.model_calls==2 and result.counters.tool_calls==1
    assert s.capacity_receipt['bound']['plain_chars']+512<=24000
    held=s.freeze(result.final_output.model_dump(),[r.model_dump() for r in result.evidence]).view()
    assert not held['generation_coverage_report']['structural_blockers']
    assert held['generation_capacity']['future_model_source_calls'] is False
    assert held['generation_plan']['target_mode']=='groups' and not held['publication_approval']
    db.close()


@pytest.mark.parametrize('case',['overflow','cancel','source','event'])
def test_pre_request_stops_have_zero_model_dispatch(tmp_path,monkeypatch,case):
    c,s,execute,record,events,db=fixture(tmp_path);adapter=ScriptedAdapter(script());cancel=[False];changed=[False]
    if case=='overflow':
        original=s.context
        def oversized(refs):v=original(refs);v['padding']='x'*24000;return v
        monkeypatch.setattr(s,'context',oversized)
    def observed(event):
        if event['type']=='tool_feedback_accepted':
            if case=='event':return False
            if case=='cancel':cancel[0]=True
            if case=='source':changed[0]=True
        return record(event)
    def guard():
        if changed[0]:raise SourceError('source_changed','Fixture version changed.')
    result=run_agent(adapter,execute,guard,lambda:cancel[0],AgentBudget(max_retries=0),observed,context(),section_generation=s)
    assert result.status==dict(overflow='budget_exhausted',cancel='cancelled',source='source_changed',event='failed')[case]
    assert result.counters.model_calls==adapter.calls==0 and not any(e['type']=='model_requested' for e in events)
    db.close()


@pytest.mark.parametrize('case',['long_gap','unknown_group','duplicate_group','model_read'])
def test_unbounded_or_foreign_plan_data_cannot_invalidate_capacity_forecast(tmp_path,case):
    c,s,execute,record,events,db=fixture(tmp_path);steps=script()
    row=steps[0]['section_plan']['criteria'][0]
    if case=='long_gap':row['gap']='x'*81
    if case=='unknown_group':row['targets']=['g:foreign']
    if case=='duplicate_group':row['targets']*=2
    if case=='model_read':steps[0]=dict(kind='tools',tool_calls=[dict(id='unwanted-model-id',name='read_excerpt',arguments=dict(path=PATH,start_line=1,end_line=5))])
    adapter=ScriptedAdapter(steps)
    result=run_agent(adapter,execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='failed' and adapter.calls==1 and result.counters.tool_calls==1
    db.close()


def test_large_permitted_envelope_cannot_make_any_native_http_or_reservation(tmp_path,monkeypatch):
    from test_deepseek_adapter import setup,KEY
    from project_evidence.deepseek_adapter import DeepSeekAdapter
    c,s,execute,record,events,db=fixture(tmp_path)
    calls=[];base,ledger=setup(tmp_path,lambda _:pytest.fail('Oversize preflight sent HTTP'))
    reserve=ledger.reserve
    def counted(*a,**k):calls.append(1);return reserve(*a,**k)
    monkeypatch.setattr(ledger,'reserve',counted)
    original=s.context
    def oversized(refs):v=original(refs);v['padding']='x'*24000;return v
    monkeypatch.setattr(s,'context',oversized)
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,enable_submission=True,goal='section_explanation',topic='submission',section_protocol='named_functions',section_read_ranges={PATH:(1,5)}).for_task(c.scope.task_id,goal='section_explanation',topic='submission')
    result=run_agent(adapter,execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='budget_exhausted' and result.counters.model_calls==0 and calls==[]
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0
    db.close()


def test_native_mock_two_calls_share_original_ledger_after_preload(tmp_path,monkeypatch):
    import httpx
    from test_deepseek_adapter import setup,KEY
    from project_evidence.deepseek_adapter import DeepSeekAdapter
    from test_section_named_adapter import response
    from test_section_native_contract import call
    c,s,execute,record,events,db=fixture(tmp_path);requests=[];reservations=[]
    def respond(request):
        payload=json.loads(request.content);requests.append(payload)
        assert any(e['type']=='generation_capacity_checked' for e in events)
        if len(requests)==1:return httpx.Response(200,json=response(call('submit_coverage_plan',script()[0]['section_plan'])))
        args={k:v for k,v in script()[1]['final_output'].items() if k not in ('kind','topic','section_id')}
        args['title']=context().anchor.section_title;args['coverage_claims']=script()[1]['coverage_claims']
        return httpx.Response(200,json=response(call('publish_section',args)))
    base,ledger=setup(tmp_path,respond);reserve=ledger.reserve
    def counted(*a,**k):reservations.append(1);return reserve(*a,**k)
    monkeypatch.setattr(ledger,'reserve',counted)
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',transport=base._transport,enable_submission=True,goal='section_explanation',topic='submission',section_protocol='named_functions',section_read_ranges={PATH:(1,5)}).for_task(c.scope.task_id,goal='section_explanation',topic='submission')
    result=run_agent(adapter,execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    assert result.status=='completed' and len(requests)==len(reservations)==2
    assert [p['max_tokens'] for p in requests]==[4000,3000]
    assert result.counters.output_tokens==2000 and result.counters.tool_calls==1
    assert ledger.summary('first-trial')['unresolved_requests']==0
    db.close()


@pytest.mark.parametrize('text,blocked',[('g:writes',True),('写入状态是 DONE。',False)])
def test_group_presence_never_certifies_member_semantics(tmp_path,text,blocked):
    c,s,execute,record,events,db=fixture(tmp_path);steps=script()
    steps[1]['final_output']['blocks'][0]['text']=text;steps[1]['coverage_claims'][0]['quote']=text
    result=run_agent(ScriptedAdapter(steps),execute,lambda:None,lambda:False,AgentBudget(max_retries=0),record,context(),section_generation=s)
    held=s.freeze(result.final_output.model_dump(),[r.model_dump() for r in result.evidence]).view()
    report=held['generation_coverage_report']
    assert bool(report['structural_blockers']) is blocked
    assert not report['semantic_verified'] and not held['publication_approval']
    assert report['target_mode']=='groups'
    db.close()
