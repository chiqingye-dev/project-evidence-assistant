"""Generation-guidance integration and old-record compatibility, without billing."""
from hashlib import sha256
import pytest
from project_evidence import deepseek_adapter as adapter
from project_evidence.agent_protocol import GOAL,OUTLINE_GOAL,SECTION_GOAL,FOLLOW_UP_GOAL
from project_evidence.learning_contract import SectionExplanation


@pytest.mark.parametrize('topic', ['query','submission','consumption','judging'])
@pytest.mark.parametrize('goal', [OUTLINE_GOAL,SECTION_GOAL])
def test_normal_learning_flow_receives_style_with_original_payload_contract(topic,goal):
    text,digest=adapter._load_guidance(goal,topic)
    assert 'learning_guide_v1' in text and '参考回答' in text and 'mermaid' in text
    assert '不强制三方案或固定图表数量' in text and len(text.encode())<=12_000
    assert digest==sha256(text.encode()).hexdigest()


def test_style_updates_have_new_identity_and_do_not_rewrite_other_goals(tmp_path,monkeypatch):
    before={goal:adapter._load_guidance(goal) for goal in (GOAL,FOLLOW_UP_GOAL)}
    style=tmp_path/'learning-guide.txt';style.write_text('learning style A',encoding='utf-8')
    monkeypatch.setattr(adapter,'LEARNING_STYLE_PATH',style)
    first=adapter._load_guidance(SECTION_GOAL)
    style.write_text('learning style B',encoding='utf-8')
    second=adapter._load_guidance(SECTION_GOAL)
    assert first[1]!=second[1]
    assert all(adapter._load_guidance(goal)==value for goal,value in before.items())


@pytest.mark.parametrize('text',['x'*6001,'api_key = synthetic-style-must-not-leak'])
def test_invalid_style_stops_before_any_model_request(tmp_path,monkeypatch,text):
    style=tmp_path/'learning-guide.txt';style.write_text(text,encoding='utf-8')
    monkeypatch.setattr(adapter,'LEARNING_STYLE_PATH',style)
    with pytest.raises(ValueError) as error:adapter._load_guidance(SECTION_GOAL)
    assert text not in str(error.value)


def test_illustrated_answer_uses_original_saved_block_shape():
    value=dict(kind='section_explanation',topic='query',section_id='entry',title='合成说明',blocks=[
        dict(category='mechanism',heading='流程',text='```mermaid\nflowchart LR\nA[输入] --> B[校验]\n```',support='inference',evidence_ids=['piece-a']),
        dict(category='mechanism',heading='面试问答：为什么先校验？',text='参考回答：这是合成推导，尚待源码核对。',support='candidate',evidence_ids=[])],gaps=['合成内容不是验收结果。'])
    assert SectionExplanation.model_validate(value).model_dump()==value


@pytest.mark.parametrize('encoding,compressed',[('gzip',True),('gzip;q=0',False)])
def test_local_diagram_asset_negotiates_compression_without_model_configuration(tmp_path,encoding,compressed):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from project_evidence.app import create_app
    with TestClient(create_app(data_dir=tmp_path/'private-test-db'),base_url='http://127.0.0.1') as client:
        response=client.get('/static/vendor/mermaid-12.3.0.min.js',headers={'Accept-Encoding':encoding})
        assert response.status_code==200 and (response.headers.get('content-encoding')=='gzip') is compressed
        assert response.headers['vary']=='Accept-Encoding'
        assert response.content==(Path(__file__).resolve().parents[1]/'static/vendor/mermaid-12.3.0.min.js').read_bytes()
        assert client.get('/api/model/status').json()['model_status']=='not_configured'
