from copy import deepcopy
import pytest
from project_evidence.claim_placement_prototype import catalog,derive
from project_evidence.generation_contract import complete,final_report
from test_generation_contract import fixture,prepare,body,claim,ready

def test_host_derives_original_pair_location_and_full_text_without_modifying_output():
    c,spec,target,plan,final=ready();output=final.view()['output'];before=deepcopy(output)
    choices=catalog(plan);claims=derive(plan,output,[[dict(id=choices[0]['id'],kind='explanation')]],[])
    assert claims==[claim(target)] and output==before
    report=final_report(plan,final,c,spec,claims)
    assert not report['findings'] and not report['semantic_verified'] and not report['publication_approval']

@pytest.mark.parametrize('case',['empty','relationship','unknown','ids_only'])
def test_missing_or_unexplained_text_still_has_original_structural_findings(case):
    c,spec,target,plan,final=ready(text='f:0, c:0, e1' if case=='ids_only' else None)
    choice=catalog(plan)[0]['id'];kind=case if case in ('relationship','unknown') else 'explanation'
    claims=derive(plan,final.view()['output'],[[] if case=='empty' else [dict(id=choice,kind=kind)]],[])
    report=final_report(plan,final,c,spec,claims)
    expected=dict(empty='missing_body_claim',relationship='relationship_requires_independent_source_review',
        unknown='declared_unknown_not_explained',ids_only='reference_list_only')[case]
    assert expected in {f['reason'] for f in report['findings']} and not report['publication_approval']

def test_same_target_in_two_objectives_and_separate_null_gap_keep_distinct_ids():
    c,refs,b,spec,target,draft=fixture(('Public one','Public two'))
    draft['criteria'][1]['gap']='Unknown receiver.'
    plan=prepare(b,c,refs,spec,draft)
    reversed_draft=deepcopy(draft);reversed_draft['criteria'].reverse()
    other=prepare(b,c,refs,spec,reversed_draft)
    assert catalog(other)==catalog(plan)
    output=body();output['gaps']=['Unknown receiver.']
    final=complete(plan,c,spec,output,refs);choices=catalog(plan)
    assert len(choices)==3 and len({r['id'] for r in choices})==3
    targets=[dict(id=r['id'],kind='explanation') for r in choices if r['target'] is not None]
    gap=[dict(id=r['id'],kind='unknown') for r in choices if r['target'] is None]
    missing=derive(plan,output,[targets],[[]]);report=final_report(plan,final,c,spec,missing)
    assert dict(number=2,target=None,reason='missing_body_claim') in report['findings']
    all_claims=derive(plan,output,[targets],[gap]);report=final_report(plan,final,c,spec,all_claims)
    assert len(all_claims)==3 and not any(f['reason']=='missing_body_claim' for f in report['findings'])
    assert not report['publication_approval']
    assert [r['number'] for r in choices]==[1,2,2]

@pytest.mark.parametrize('case',['archive','foreign','duplicate','extra','shape'])
def test_json_or_invalid_placements_cannot_mint_a_plan_or_repair_missing_choices(case):
    c,spec,target,plan,final=ready();output=final.view()['output'];row=dict(id=catalog(plan)[0]['id'],kind='explanation')
    blocks=[[row]]
    if case=='archive':plan=plan.view()
    if case=='foreign':row['id']='foreign'
    if case=='duplicate':blocks[0].append(dict(row))
    if case=='extra':row['number']=1
    if case=='shape':blocks=[]
    with pytest.raises(ValueError):derive(plan,output,blocks,[])
