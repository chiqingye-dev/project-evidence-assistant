"""Source-call sidecar binding and non-approval tests, no model or publishing."""
from copy import deepcopy
import pytest
from project_evidence.call_binding import bind
from project_evidence.call_claim_contract import review,output_digest,bind_proposals,Rejected
from test_call_binding import fixture,output


def prepared(source=None,text='send 传入回调。',kind='pass_callable'):
    index=bind(*(fixture(source,1,1) if source else fixture()))
    body=output(text)
    call=next(c for c in index.view()['sources']['evidence-one']['calls'] if c['name']=='send')
    claim=dict(index_digest=index.digest,output_digest=output_digest(body,index),call_id=call['call_id'],field='block_text',
        block=1,start=0,end=len(text),kind=kind)
    return index,body,claim


def test_supported_syntax_is_bound_but_never_semantic_or_publication_approval():
    index,body,claim=prepared();saved=deepcopy((body,claim))
    result=review(body,index,[claim])
    assert result['status']=='syntax_supported_only' and result['supported_call_syntax']
    assert result['semantic_review_required'] is True
    assert result['semantic_verified'] is result['publication_approval'] is result['publication_performed'] is False
    assert (body,claim)==saved


@pytest.mark.parametrize('case',['index','output','location','span','overlap','call','extra','bool','uncited','target'])
def test_model_sidecar_cannot_forge_binding_or_certify_itself(case):
    index,body,claim=prepared();claims=[claim]
    if case=='index':claim['index_digest']='0'*64
    if case=='output':body['blocks'][0]['text']+='改变后正文'
    if case=='location':claim['block']=2
    if case=='span':claim['end']=14000
    if case=='overlap':claims.append(deepcopy(claim))
    if case=='call':claim['call_id']='0'*32
    if case=='extra':claim['host_verified']=True
    if case=='bool':claim['start']=False
    if case=='uncited':
        body['blocks'][0].update(support='candidate',evidence_ids=[]);claim['output_digest']=output_digest(body,index)
    if case=='target':
        body['section_id']='other';claim['output_digest']='0'*64
    with pytest.raises(Rejected):review(body,index,claims)


@pytest.mark.parametrize('kind,text',[
    ('pass_callable','send 传入回调。'),('register_callable','send 登记回调。'),
    ('execute_callable','send 立即执行回调。'),('invoke','send 是传入回调的调用点。'),
])
def test_plain_value_call_or_relabelled_claim_stays_unproved(kind,text):
    index,body,claim=prepared('class X { void f() { send(value); } }',text,kind)
    result=review(body,index,[claim])
    assert result['status']=='needs_review' and result['claim_findings']


def test_missing_sidecar_or_partial_term_coverage_cannot_approve_body():
    index,body,claim=prepared()
    result=review(body,index,[])
    assert any(f['reason']=='uncovered_call_language' for f in result['claim_findings'])
    claim['end']=4  # Only the callee name, not the assertion language.
    result=review(body,index,[claim])
    assert result['status']=='needs_review'


def test_correct_call_id_does_not_certify_a_different_receiver_or_execution():
    index,body,claim=prepared(text='different.send 传入回调并立即执行。')
    result=review(body,index,[claim])
    assert any(f['reason']=='claim_target_not_in_span' for f in result['claim_findings'])


def test_invoke_only_claim_has_narrow_support_and_still_requires_semantic_review():
    index,body,claim=prepared(text='send 是一个调用点。',kind='invoke')
    result=review(body,index,[claim])
    assert result['status']=='syntax_supported_only'
    assert result['supported_call_syntax'][0]['syntax_support']=='invocation_exists'
    assert result['publication_approval'] is False


def test_host_binds_quote_and_digest_without_model_hash_or_offset_arithmetic():
    index,body,claim=prepared()
    proposal={k:claim[k] for k in ('call_id','field','block','kind')}
    proposal['quote']=body['blocks'][0]['text']
    bound=bind_proposals(body,index,[proposal])
    assert bound[0]['output_digest']==output_digest(body,index) and bound[0]['start']==0
    assert review(body,index,bound)['publication_approval'] is False


@pytest.mark.parametrize('case',['missing_quote','repeated_quote','forged_digest','wrong_field','mutated_body'])
def test_proposal_seal_cannot_guess_text_or_accept_forged_host_fields(case):
    index,body,claim=prepared()
    proposal={k:claim[k] for k in ('call_id','field','block','kind')};proposal['quote']=body['blocks'][0]['text']
    if case=='missing_quote':proposal['quote']='不存在的原文'
    if case=='repeated_quote':body['blocks'][0]['text']*=2
    if case=='forged_digest':proposal['output_digest']='0'*64
    if case=='wrong_field':proposal['block']=2
    if case=='mutated_body':body['blocks'][0]['text']='正文已改变'
    with pytest.raises(Rejected):bind_proposals(body,index,[proposal])
