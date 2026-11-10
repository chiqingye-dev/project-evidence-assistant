"""Pinned inputs fail closed before any billing; copy only manifest-owned files."""
import hashlib
import json
from pathlib import Path

import pytest
from project_evidence.agent_protocol import ModelError
from project_evidence import section_native_binding as binding
from test_section_named_adapter import configured
from test_section_function_carrier import chosen_context


def copy_inputs(tmp_path,monkeypatch):
    original=binding.HERE
    manifest=json.loads((original/binding.MANIFEST).read_text(encoding='utf-8'))
    for name in [binding.MANIFEST,*manifest]:
        path=tmp_path/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((original/name).read_bytes())
    monkeypatch.setattr(binding,'HERE',tmp_path)
    return manifest


def test_native_seal_and_both_original_followup_seals():
    from project_evidence.followup_scope_columns_binding import verify_inputs as columns
    from project_evidence.followup_index_columns_binding import verify_inputs as indexed
    assert len(binding.verify_inputs())==len(columns())==len(indexed())==64


@pytest.mark.parametrize('name',[
    'src/project_evidence/deepseek_adapter.py','src/project_evidence/agent_protocol.py',
    'src/project_evidence/agent_engine.py','src/project_evidence/source_ranges.py',
    'src/project_evidence/section_native_contract.py','src/project_evidence/trial_readiness.py',
    'prompts/submission-native-section.txt','prompts/learning-guide.txt',
])
def test_input_drift_blocks_opt_in_construction_before_any_request(tmp_path,monkeypatch,name):
    copy_inputs(tmp_path/'sealed',monkeypatch)
    assert binding.verify_inputs()==binding.EXPECTED
    path=binding.HERE/name; path.write_bytes(path.read_bytes()+b'\n# drift')
    with pytest.raises(ModelError) as error:configured(tmp_path/'store',lambda _:pytest.fail('Changed inputs sent'))
    assert error.value.code=='not_authorized' and error.value.request_sent is False


def test_manifest_drift_is_not_silently_resealed(tmp_path,monkeypatch):
    copy_inputs(tmp_path,monkeypatch)
    path=tmp_path/binding.MANIFEST; path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ModelError):binding.verify_inputs()


def test_drift_after_construction_still_rejects_before_reservation(tmp_path,monkeypatch):
    copy_inputs(tmp_path/'sealed',monkeypatch)
    adapter,ledger=configured(tmp_path/'store',lambda _:pytest.fail('Changed inputs sent'))
    path=binding.HERE/'src/project_evidence/section_native_contract.py'
    path.write_bytes(path.read_bytes()+b'\n# drift after construction')
    with pytest.raises(ModelError) as error:adapter.generate(chosen_context(),timeout_seconds=5,max_output_tokens=4000)
    assert error.value.request_sent is False and ledger.summary('first-trial')['accounted_upper_bound_microyuan']==0
