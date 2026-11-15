"""Request guidance uses the same selector grammar and obligations as the host.

The ninth-task fixture below retains only its public plan shape. No missing
provider fields, private answers, or failed original output are reconstructed.
"""
import json

import pytest
from project_evidence import section_native_contract as native
from project_evidence.agent_protocol import ModelContext
from project_evidence.generation_contract import final_report, complete
from project_evidence.generation_diagnostics import GenerationRejected
from project_evidence.section_semantic_review import fields
from test_bound_claim_schema import prose
from test_generation_contract import ready, claim, fixture, body, prepare
from test_section_native_contract import context


def publication(value, profile='compact_v1'):
    return next(t['function'] for t in native.functions(ModelContext.model_validate(value),
        {'src/Example.java':((4,4),)}, output_profile=profile, max_output_tokens=4000)
        if t['function']['name']=='publish_section')


@pytest.mark.parametrize('profile,blocks,gaps',[('off',12,12),('compact_v1',6,3)])
def test_selectors_match_host_grammar_and_effective_profile_bounds(profile,blocks,gaps):
    fn=publication(prose(),profile);schema=fn['parameters']['properties']
    block=dict(heading='Heading',text='Text')
    output=dict(title='Title',blocks=[block]*blocks,gaps=['Unknown']*gaps)
    selectors=schema['coverage_claims']['items']['properties']['field']
    assert set(selectors['enum'])==set(fields(output))
    assert len(selectors['enum'])==1+2*blocks+gaps
    assert f'block:{blocks+1}:text' not in selectors['enum']
    assert 'block:0:text' not in selectors['enum'] and 'blocks[0].text' not in selectors['enum']
    assert 'one-based' in selectors['description'] and fn['strict'] is False
    assert 'block:1:text' in fn['description'] and 'gap:1' in fn['description']


@pytest.mark.parametrize('count,gap,all_gap,expected',[(1,False,False,1),(2,True,False,4),
    (12,False,False,12),(2,True,True,2)])
def test_claim_count_includes_each_target_and_separate_planned_gap(count,gap,all_gap,expected):
    fn=publication(prose(count,gap,all_gap));rows=fn['parameters']['properties']['coverage_claims']
    assert rows['minItems']==rows['maxItems']==expected
    assert f'exactly {expected} coverage claims' in fn['description']
    reversed_value=prose(count,gap,all_gap)
    reversed_value['history'][0]['host_generation_coverage']['criteria'].reverse()
    assert publication(reversed_value)==fn


def test_ninth_public_plan_shape_requests_eight_claims_including_number_two_null():
    value=prose();control=value['history'][0]['host_generation_coverage']
    control['criteria']=[dict(number=1,targets=control['group_keys'],gap=''),
        dict(number=2,targets=['g:calls'],gap='x'*39)] # Synthetic placeholder, not recovered gap.
    fn=publication(value);rows=fn['parameters']['properties']['coverage_claims']
    assert rows['minItems']==rows['maxItems']==8
    assert None in rows['items']['properties']['target']['enum']
    assert 'PLUS one separate (same number,null)' in fn['description']
    assert 'even when that criterion also has targets' in fn['description']


@pytest.mark.parametrize('selector',['blocks[0].text','block:0:text','g:writes','block:2:text'])
def test_provider_ignoring_enum_or_using_unwritten_possible_field_is_still_rejected(selector):
    c,spec,target,plan,final=ready()
    assert ('block:2:text' in publication(prose())['parameters']['properties']
        ['coverage_claims']['items']['properties']['field']['enum'])
    with pytest.raises(GenerationRejected) as caught:
        final_report(plan,final,c,spec,[claim(target,field=selector)])
    assert caught.value.reason=='body_claim_field'


@pytest.mark.parametrize('text,quote',[('same same','same'),('Written only.','Unwritten.')])
def test_advertised_selector_does_not_bypass_exact_unique_quote(text,quote):
    c,spec,target,plan,final=ready(text=text)
    with pytest.raises(GenerationRejected) as caught:
        final_report(plan,final,c,spec,[claim(target,quote=quote)])
    assert caught.value.reason=='body_claim_quote'


def test_target_with_gap_requires_an_additional_null_claim_without_awarding_coverage():
    c,refs,b,spec,target,draft=fixture()
    draft['criteria'][0]['gap']='Unknown receiver.'
    plan=prepare(b,c,refs,spec,draft)
    output=body();output['gaps']=['Unknown receiver.']
    final=complete(plan,c,spec,output,refs)
    only_target=final_report(plan,final,c,spec,[claim(target)])
    assert dict(number=1,target=None,reason='missing_body_claim') in only_target['findings']
    result=final_report(plan,final,c,spec,[claim(target),claim(None,field='gap:1',
        quote='Unknown receiver.',kind='unknown')])
    reasons={r['reason'] for r in result['findings']}
    assert 'missing_body_claim' not in reasons and 'declared_unknown_not_explained' in reasons
    assert not result['semantic_verified'] and not result['publication_approval']


def test_stage_aware_guidance_preserves_legacy_schema_and_loads_within_original_limit():
    guide,_=native.load_guidance()
    assert len(guide.encode())<=12000 and '参数只有title、blocks、gaps' not in guide
    assert '若本轮Schema含coverage_claims' in guide and '索引从1开始' in guide
    assert '建议6～8' not in guide
    legacy=publication(context().model_dump())
    assert 'coverage_claims' not in legacy['parameters']['properties']
    assert set(legacy['parameters']['required'])=={'title','blocks'}
    request=native.request_body(ModelContext.model_validate(prose()),{'src/Example.java':((4,4),)},
        guide,max_output_tokens=4000,output_profile='compact_v1')
    assert 'PRIVATE_EXPECTED_ANSWER' not in json.dumps(request)
    assert request['max_tokens']==4000
