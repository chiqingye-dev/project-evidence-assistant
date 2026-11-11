"""Independent-review contract tests; scripted judgements are not model quality."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import pytest
from project_evidence.section_semantic_review import ReviewContract,HostReviewer,CheckedReview,assess,packet
from project_evidence.review_contracts import submission_contract,SUBMISSION_GOLDEN_SHA256
from test_section_review import collected
from test_call_binding import output


def contract(scope):
    raw=json.dumps(dict(commit=scope.commit,public_url=scope.public_url,tasks=[dict(id='FIXTURE',
        goal='section_explanation',required=['Explain this invocation.','Retain the unread receiver boundary.'])])).encode()
    return ReviewContract.from_document(raw,sha256(raw).hexdigest(),'FIXTURE')


def assessment(view):
    body=view['output']['blocks'][0]['text'];eid,source=next(iter(view['source'].items()))
    return dict(task_id=view['task_id'],output_digest=view['output_digest'],index_digest=view['index_digest'],
        contract_digest=view['contract_digest'],covered_fields=[{k:f[k] for k in ('field','sha256')} for f in view['fields']],
        criteria=[dict(number=i,verdict='pass',reason='Synthetic fixture review only.',
            body_quotes=[dict(field='block:1:text',quote=body)],source_quotes=[dict(evidence_id=eid,
                start_line=source['start_line'],end_line=source['end_line'],quote=source['text'])]) for i in range(1,len(view['required'])+1)],
        call_proposals=[],critical_findings=[])


def prepared(text='send 是已读调用。'):
    collector,refs=collected();candidate=collector.freeze(output(text),refs)
    return collector,candidate,contract(collector.scope)


def test_pinned_original_s03_preserves_all_six_and_the_frozen_document():
    spec=submission_contract()
    assert spec.document_sha256==SUBMISSION_GOLDEN_SHA256 and len(spec.required)==6 and spec.case_id=='M2-S03'
    assert 'afterCommit' in spec.required[-1] and 'FOR UPDATE' in spec.required[1]
    with pytest.raises(ValueError):submission_contract('M2-S01')


def test_full_scripted_review_mints_an_opaque_result_without_semantic_proof():
    collector,candidate,spec=prepared();before=candidate.view()
    result=assess(candidate,collector,spec,HostReviewer('fixture','scripted',assessment),'scripted')
    data=result.view()
    assert data['source_assisted_review_passed'] and data['publication_approval']
    assert not data['publication_performed'] and not data['semantic_verified'] and not data['personal_acceptance']
    assert data['review_mode']=='scripted' and candidate.view()==before
    data['publication_approval']=False
    assert result.view()['publication_approval']
    with pytest.raises(ValueError):CheckedReview(data)
    with pytest.raises(ValueError):result._text='forged'


@pytest.mark.parametrize('case',['task','output','index','contract','coverage','duplicate_field','field_hash','criterion_missing',
    'criterion_duplicate','no_body','no_source','body_changed','source_changed','outside_read','unread_id','extra',
    'bool','local_reason','invented_finding','finding_reason'])
def test_incomplete_forged_or_unbound_review_cannot_approve(case):
    collector,candidate,spec=prepared()
    def changed(view):
        data=assessment(view);item=data['criteria'][0]
        if case=='task':data['task_id']='other'
        if case in ('output','index','contract'):data[case+'_digest']='0'*64
        if case=='coverage':data['covered_fields'].pop()
        if case=='duplicate_field':data['covered_fields'].append(deepcopy(data['covered_fields'][0]))
        if case=='field_hash':data['covered_fields'][0]['sha256']='0'*64
        if case=='criterion_missing':data['criteria'].pop()
        if case=='criterion_duplicate':data['criteria'][1]['number']=1
        if case=='no_body':item['body_quotes']=[]
        if case=='no_source':item['source_quotes']=[]
        if case=='body_changed':item['body_quotes'][0]['quote']='unwritten'
        if case=='source_changed':item['source_quotes'][0]['quote']='unread source'
        if case=='outside_read':item['source_quotes'][0]['start_line']=1
        if case=='unread_id':item['source_quotes'][0]['evidence_id']='not-read'
        if case=='extra':data['semantic_verified']=True
        if case=='bool':item['number']=True
        if case=='local_reason':item['reason']='C:/Users/private/config'
        if case=='invented_finding':data['critical_findings']=[dict(field='title',quote='unwritten',reason='fixture')]
        if case=='finding_reason':data['critical_findings']=[dict(field='block:1:text',quote=view['output']['blocks'][0]['text'],reason='C:/Users/private/config')]
        return data
    with pytest.raises(ValueError):assess(candidate,collector,spec,HostReviewer('fixture','scripted',changed),'scripted')


@pytest.mark.parametrize('case',['fail','unverified','critical','call_audit'])
def test_recognized_unsupported_claim_or_any_unpassed_criterion_vetoes_release(case):
    collector,candidate,spec=prepared('send 登记并执行回调。' if case=='call_audit' else 'send 是已读调用。')
    def judge(view):
        data=assessment(view)
        if case in ('fail','unverified'):data['criteria'][0]['verdict']=case
        if case=='critical':data['critical_findings']=[dict(field='block:1:text',quote=view['output']['blocks'][0]['text'],reason='Unproved fixture statement.')]
        return data
    data=assess(candidate,collector,spec,HostReviewer('fixture','scripted',judge),'scripted').view()
    assert not data['publication_approval'] and not data['source_assisted_review_passed']
    assert not data['publication_performed'] and not data['semantic_verified']


def test_a_different_or_reopened_json_view_cannot_be_a_live_candidate():
    collector,candidate,spec=prepared()
    with pytest.raises(ValueError):assess(candidate.view(),collector,spec,HostReviewer('fixture','scripted',assessment),'scripted')
    with pytest.raises(ValueError):assess(candidate,collector,replace(spec,commit='b'*40),HostReviewer('fixture','scripted',assessment),'scripted')
    with pytest.raises(ValueError):assess(candidate,collector,spec,HostReviewer('fixture','scripted',assessment),'real')


def test_callback_packet_mutation_cannot_change_the_candidate_or_live_source():
    collector,candidate,spec=prepared();before=candidate.view()
    def judge(view):
        data=assessment(view);view['output']['title']='changed';view['source'].clear();return data
    assert assess(candidate,collector,spec,HostReviewer('fixture','scripted',judge),'scripted').view()['publication_approval']
    assert candidate.view()==before and collector._accepted


def test_body_overlap_and_foreign_citation_are_not_unique_bound_support():
    collector,candidate,spec=prepared('aaa')
    def judge(view):
        data=assessment(view);data['criteria'][0]['body_quotes'][0]['quote']='aa';return data
    with pytest.raises(ValueError):assess(candidate,collector,spec,HostReviewer('fixture','scripted',judge),'scripted')
    collector,candidate,spec=prepared()
    def uncited(view):
        data=assessment(view);data['criteria'][0]['body_quotes']=[dict(field='title',quote=view['output']['title'])];return data
    with pytest.raises(ValueError):assess(candidate,collector,spec,HostReviewer('fixture','scripted',uncited),'scripted')


@pytest.mark.parametrize('case',['sha','goal','missing','duplicate','empty','too_many'])
def test_contract_cannot_silently_reduce_or_replace_the_selected_frozen_case(case):
    raw=dict(commit='a'*40,public_url='https://github.com/example/public-java',tasks=[dict(id='FIXTURE',goal='section_explanation',required=['One.'])])
    if case=='goal':raw['tasks'][0]['goal']='directory'
    if case=='missing':raw['tasks']=[]
    if case=='duplicate':raw['tasks']*=2
    if case=='empty':raw['tasks'][0]['required']=[]
    if case=='too_many':raw['tasks'][0]['required']=[str(i) for i in range(13)]
    data=json.dumps(raw).encode();expected='0'*64 if case=='sha' else sha256(data).hexdigest()
    with pytest.raises(ValueError):ReviewContract.from_document(data,expected,'FIXTURE')
