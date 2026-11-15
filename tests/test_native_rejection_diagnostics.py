from copy import deepcopy
import json
import pytest
from project_evidence import section_native_contract as native
from project_evidence.native_rejection_diagnostics import projection,safe_native_rejection
from project_evidence.agent_protocol import ModelError
from project_evidence.task_diagnostics import engine_metadata,safe_metadata
from test_section_native_contract import context,publication,response,call,RANGES,USAGE

CASES=('missing','extra','title','blocks','gaps','gap_chars','heading','text','evidence')
EXPECTED=dict(missing='keys',extra='keys',title='title',blocks='compact_blocks',gaps='compact_gaps',
    gap_chars='compact_gap_chars',heading='compact_heading_chars',text='compact_text_chars',evidence='compact_evidence_count')

def bad_args(case,args):
    if case=='missing':args.pop('title')
    if case=='extra':args['SYNTHETIC_UNKNOWN_PRIVATE_FIELD']='SYNTHETIC_PRIVATE_VALUE'
    if case=='title':args['title']='SYNTHETIC_PRIVATE_VALUE'
    if case=='blocks':args['blocks']=[deepcopy(args['blocks'][0]) for _ in range(7)]
    if case=='gaps':args['gaps']=['Unknown1','Unknown2','Unknown3','Unknown4']
    if case=='gap_chars':args['gaps']=['x'*101]
    if case=='heading':args['blocks'][0]['heading']='x'*49
    if case=='text':args['blocks'][0]['text']='x'*321
    if case=='evidence':args['blocks'][0]['evidence_ids']=['e1','e2','e3','e4']
    return args

@pytest.mark.parametrize('case',CASES)
def test_each_old_shape_rejection_has_safe_exact_branch(case):
    args=bad_args(case,publication());before=deepcopy(args)
    with pytest.raises(native.Rejected) as error:
        native.decode(response(call('publish_section',args)),context(),RANGES,USAGE,output_profile='compact_v1',max_output_tokens=4000)
    assert error.value.reason=='arguments_shape' and args==before
    value=error.value.native_rejection
    assert value['reason']==EXPECTED[case] and value['function']=='publish_section'
    if case=='missing':assert value['missing_fields']==['title']
    if case=='extra':assert value['extra_fields_count']==1
    if case=='text':assert (value['observed'],value['limit'],value['block_index'])==(321,320,1)
    assert 'SYNTHETIC_' not in json.dumps(value)
    event=dict(type='model_error',details=dict(native_rejection=value,generation_check=dict(step='native_decode',reason='native_rejected')))
    assert engine_metadata(event)['native_rejection']==value
    assert safe_metadata(engine_metadata(event))['native_rejection']==value

@pytest.mark.parametrize('case',['unknown','bool','extra','missing','function','phase','reason','duplicate'])
def test_model_error_and_storage_drop_forged_or_private_projection(case):
    value=projection('prose','publish_section','keys')
    if case=='unknown':value['missing_fields']=['SYNTHETIC_PRIVATE_VALUE']
    if case=='bool':value['observed']=True
    if case=='extra':value['raw']='SYNTHETIC_PRIVATE_VALUE'
    if case=='missing':value.pop('format')
    if case=='function':value['function']='SYNTHETIC_PRIVATE_VALUE'
    if case=='phase':value['phase']='SYNTHETIC_PRIVATE_VALUE'
    if case=='reason':value['reason']='SYNTHETIC_PRIVATE_VALUE'
    if case=='duplicate':value['missing_fields']=['title','title']
    assert safe_native_rejection(value) is None
    assert ModelError('invalid_response',native_rejection=value).native_rejection is None
    meta=safe_metadata(dict(native_rejection=value,generation_check=dict(step='native_decode',reason='native_rejected')))
    assert 'native_rejection' not in meta and 'generation_check' not in meta

def test_ordinary_model_error_does_not_invent_native_diagnostics():
    assert ModelError('invalid_response').native_rejection is None
    assert safe_metadata(dict(generation_check=dict(step='native_decode',reason='native_rejected')))=={}
