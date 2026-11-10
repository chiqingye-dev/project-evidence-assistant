"""Reviewed input seals accept the build and still reject drift before billing."""
import json
from pathlib import Path
import shutil

import httpx
import pytest

from project_evidence import followup_index_columns_binding as index
from project_evidence import followup_scope_columns_binding as columns
from project_evidence.agent_protocol import FOLLOW_UP_GOAL, ModelContext, ModelError
from project_evidence.deepseek_adapter import DeepSeekAdapter
from project_evidence.followup_index_columns_contract import MODE as INDEX
from project_evidence.followup_scope_columns_codec import FORMAT as COLUMNS
from project_evidence.followup_table_contract import FORMAT as TABLE
from project_evidence.followup_prose_kind import FLAT_FORMAT
from test_deepseek_adapter import setup, KEY
from test_followup import followup_anchor
from test_agent_engine import CONTEXT


def isolated_inputs(tmp_path, monkeypatch):
    source=Path(__file__).resolve().parents[1]
    target=tmp_path/'sealed-inputs'
    for module in (columns,index):
        for name in (module.MANIFEST,*json.loads((source/module.MANIFEST).read_text(encoding='utf-8'))):
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():shutil.copyfile(source/name,dest)
        monkeypatch.setattr(module,'HERE',target)
    return target


def preparation(tmp_path, mode):
    requests=[]
    base,ledger=setup(tmp_path,lambda _: requests.append(1) or pytest.fail('Preparation must not dispatch'))
    adapter=DeepSeekAdapter(KEY,ledger,'first-trial',enable_query=True,enable_followup=True,
        transport=base._transport,followup_protocol='two_phase',followup_scope_view=mode,
        followup_claim_format=TABLE,followup_prose_format=FLAT_FORMAT).for_task('sealed-preparation',goal=FOLLOW_UP_GOAL)
    context=ModelContext(**CONTEXT,goal=FOLLOW_UP_GOAL,anchor=followup_anchor())
    def prepare():
        return adapter.prepare_phase(context,phase='plan_or_tools',accepted={},context_limit=24000,
            previous_context_chars=0,timeout_seconds=5,max_output_tokens=4000)
    return adapter,ledger,requests,prepare


@pytest.mark.parametrize('mode',[COLUMNS,INDEX])
def test_reviewed_seals_allow_pure_native_preparation(tmp_path,mode):
    assert columns.verify_inputs()==columns.EXPECTED
    assert index.verify_inputs()==index.EXPECTED
    adapter,ledger,requests,prepare=preparation(tmp_path,mode)
    prepared=prepare()
    assert prepared is adapter._prepared_phase and json.loads(prepared.request_json)['model']=='deepseek-flash'
    assert not requests and ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0
    with ledger.store._connection() as c:assert c.execute('SELECT count(*) FROM model_spend').fetchone()[0]==0


@pytest.mark.parametrize('mode',[COLUMNS,INDEX])
@pytest.mark.parametrize('target',['manifest','input','missing'])
def test_changed_sealed_inputs_reject_before_preparation_or_reservation(tmp_path,monkeypatch,mode,target):
    isolated=isolated_inputs(tmp_path,monkeypatch)
    module=columns if mode==COLUMNS else index
    p=isolated/(module.MANIFEST if target=='manifest' else 'src/project_evidence/deepseek_adapter.py')
    if target=='missing':p.unlink()
    else:p.write_bytes(p.read_bytes()+b'\nsynthetic-secret-must-not-leak')
    adapter,ledger,requests,prepare=preparation(tmp_path,mode)
    with pytest.raises(ModelError) as error:prepare()
    assert error.value.code=='not_authorized' and error.value.request_sent is False
    assert 'synthetic-secret' not in str(error.value) and adapter._prepared_phase is None
    assert not requests and ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0
    with ledger.store._connection() as c:assert c.execute('SELECT count(*) FROM model_spend').fetchone()[0]==0


@pytest.mark.parametrize('mode',[COLUMNS,INDEX])
def test_changed_input_after_preparation_consumes_snapshot_without_dispatch(tmp_path,monkeypatch,mode):
    isolated=isolated_inputs(tmp_path,monkeypatch)
    adapter,ledger,requests,prepare=preparation(tmp_path,mode)
    prepared=prepare()
    p=isolated/'src/project_evidence/agent_protocol.py';p.write_bytes(p.read_bytes()+b'\n# synthetic drift')
    with pytest.raises(ModelError) as error:adapter.generate_prepared_phase(prepared,timeout_seconds=5)
    assert error.value.code=='not_authorized' and error.value.request_sent is False
    assert adapter._prepared_phase is None and not requests
    assert ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0
    with ledger.store._connection() as c:assert c.execute('SELECT count(*) FROM model_spend').fetchone()[0]==0
