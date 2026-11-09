"""Reject extra top-level content, retain nested diagrams and settled usage."""
import json
import httpx
import pytest
from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import ModelContext,ModelError,SECTION_GOAL
from project_evidence.deepseek_adapter import DeepSeekAdapter,_json_extra_details
from test_deepseek_adapter import setup,envelope,context,KEY
from test_learning_engine import anchor

@pytest.mark.parametrize('suffix,label',[
    (' {}','json.extra_object'),(' []','json.extra_array'),
    (' '+chr(96)*3+'mermaid\nprivate '+KEY,'json.extra_fence'),
    (' }','json.extra_closer'),(' ]','json.extra_closer'),
    (' private '+KEY,'json.extra_other'),(' 123','json.extra_other'),
])
def test_extra_suffix_is_rejected_without_repair_retry_or_disclosure(tmp_path,suffix,label):
    calls=[];content=envelope()['choices'][0]['message']['content']+suffix
    def respond(request):calls.append(1);return httpx.Response(200,json=envelope(content))
    adapter,ledger=setup(tmp_path,respond)
    result=run_agent(adapter,lambda *_:pytest.fail('Rejected output must not invoke tools'),lambda:None,lambda:False,initial_context=context())
    logged=result.model_dump_json()
    assert result.status=='failed' and result.final_output is None and len(calls)==1
    assert 'action.json_invalid' in logged and 'json.extra_data' in logged and label in logged
    assert KEY not in logged and 'private' not in logged and content not in logged
    summary=ledger.summary('first-trial')
    assert summary['unresolved_requests']==0 and summary['accounted_upper_bound_microyuan']==28000

@pytest.mark.parametrize('invalid',['{', '{"text":"unterminated', '{"x":1,}', '```json\n{}\n```'])
def test_extra_detail_does_not_reclassify_other_json_errors(invalid):
    with pytest.raises(json.JSONDecodeError) as error:json.loads(invalid)
    assert _json_extra_details(error.value)==()

def test_escaped_diagram_and_answer_remain_inside_one_section_object(tmp_path):
    fence=chr(96)*3
    diagram=f'{fence}mermaid\nflowchart LR\n A["input"] --> B["response"]\n{fence}'
    answer='参考回答：这里的引号 "sample" 与反斜杠 \\ 都是文本；仍需真实源码验证。'
    section=dict(kind='section_explanation',topic='query',section_id='entry',title='合成编码检查',blocks=[
        dict(category='processing',heading='合成链路',text=diagram,support='candidate',evidence_ids=[]),
        dict(category='mechanism',heading='学习检查：怎样说明边界？',text=answer,support='candidate',evidence_ids=[])],gaps=['合成结果不证明模型质量。'])
    content=json.dumps(dict(kind='final',final_output=section),ensure_ascii=False)
    first,ledger=setup(tmp_path,lambda _:httpx.Response(200,json=envelope(content)))
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',enable_query=True,transport=first._transport).for_task('nested-strings',goal=SECTION_GOAL)
    ctx=ModelContext(commit='a'*40,public_url='https://github.com/example/public-java',goal=SECTION_GOAL,anchor=anchor(True))
    turn=adapter.generate(ctx,timeout_seconds=5,max_output_tokens=4000)
    assert turn.final_output.blocks[0].text==diagram and turn.final_output.blocks[1].text==answer
    assert ledger.summary('first-trial')['unresolved_requests']==0

def test_extra_diagnostic_whitelist_cannot_export_arbitrary_suffix():
    e=ModelError('invalid_response',diagnostics=('json.extra_object','json.extra_object.'+KEY,KEY))
    assert e.diagnostics==('json.extra_object',)
