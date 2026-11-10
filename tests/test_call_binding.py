"""Pure host-binding checks over synthetic snapshots, not live source access."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha1,sha256
import json
import pytest
from project_evidence.call_binding import HostScope,FileSeal,BoundIndex,Rejected,bind,audit_bound_output,model_view
from project_evidence.tools import _lines

PATH='src/Example.java'
SOURCE='class Example {\nvoid f() {\n  outside();\n  send(() -> work());\n  after();\n}\n}\n'


def fixture(source=SOURCE,start=4,end=4,spans=None):
    raw=source.encode();sha=sha256(raw).hexdigest();oid=sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    scope=HostScope('task-one','project-one','https://github.com/example/public-java','a'*40,'submission','entry',
        (FileSeal(PATH,sha,oid,spans or ((1,len(source.splitlines())),)),))
    ref={'evidence_id':'evidence-one','path':PATH,'public_url':scope.public_url,'commit':scope.commit,'blob_oid':oid,
        'sha256':sha,'start_line':start,'end_line':end,'truncated':False}
    chunks={'evidence-one':{'task_id':scope.task_id,'project_id':scope.project_id,'evidence_id':'evidence-one',
        'text':'\n'.join(_lines(source)[start-1:end])}}
    return scope,[ref],chunks,{PATH:raw}


def output(text='send 传入回调。'):
    return {'kind':'section_explanation','topic':'submission','section_id':'entry','title':'调用边界',
        'blocks':[{'category':'mechanism','heading':'机制','text':text,'support':'fact','evidence_ids':['evidence-one']}],'gaps':[]}


def test_binding_only_exports_accepted_spans_without_manual_lexical_boolean():
    values=fixture();saved=deepcopy(values)
    index=bind(*values);view=index.view()
    assert values==saved
    assert len(index.digest)==64 and len(view['sources']['evidence-one']['calls'])==2
    encoded=json.dumps(view)
    assert 'outside' not in encoded and 'after' not in encoded
    assert view['sources']['evidence-one']['calls'][0]['passing']=='explicit_callable'
    assert audit_bound_output(output(),index)['status']=='within_prototype_scope'
    assert audit_bound_output(output(),index)['publication_approval'] is False


@pytest.mark.parametrize('case',['bytes','fragment','commit','repository','blob','digest','task','project','id',
    'truncated','outside','cross_gap','duplicate','extra_blob','missing_blob','manual_context','bad_path_type'])
def test_forged_or_wrongly_bound_material_fails_closed(case):
    scope,refs,chunks,blobs=fixture()
    if case=='bytes':blobs[PATH]+=b'\n'
    if case=='fragment':chunks['evidence-one']['text']='fake(() -> work());'
    if case=='commit':refs[0]['commit']='b'*40
    if case=='repository':refs[0]['public_url']='https://github.com/other/repo'
    if case=='blob':refs[0]['blob_oid']='c'*40
    if case=='digest':refs[0]['sha256']='d'*64
    if case=='task':chunks['evidence-one']['task_id']='other-task'
    if case=='project':chunks['evidence-one']['project_id']='other-project'
    if case=='id':chunks['evidence-one']['evidence_id']='unread'
    if case=='truncated':refs[0]['truncated']=True
    if case=='outside':scope=replace(scope,files=(replace(scope.files[0],spans=((1,3),)),))
    if case=='cross_gap':
        scope=replace(scope,files=(replace(scope.files[0],spans=((1,3),(5,7))),));refs[0].update(start_line=3,end_line=5)
    if case=='duplicate':refs*=2
    if case=='extra_blob':blobs['src/Other.java']=b'not admitted'
    if case=='missing_blob':blobs.clear()
    if case=='manual_context':chunks['evidence-one']['lexical_context_verified']=True
    if case=='bad_path_type':refs[0]['path']=[]
    with pytest.raises(Rejected):bind(scope,refs,chunks,blobs)


@pytest.mark.parametrize('source,line',[
    ('/* begin\nsend(() -> fake());\n*/\nreal(1);',2),
    ('String text="""\nsend(() -> fake());\n""";\nreal(1);',2),
])
def test_full_blob_context_prevents_fake_calls_inside_prefix_comments_or_literals(source,line):
    index=bind(*fixture(source,line,line))
    assert index.view()['sources']['evidence-one']['calls']==[]
    assert audit_bound_output(output(),index)['status']=='needs_review'


@pytest.mark.parametrize('start,end',[(2,2),(3,3),(4,4)])
def test_call_crossing_admitted_span_cannot_be_stitched_from_full_context(start,end):
    source='class X { void f() {\n first.\n send(\n () -> work());\n} }'
    index=bind(*fixture(source,start,end))
    assert not any(c['name']=='send' for c in index.view()['sources']['evidence-one']['calls'])


def test_two_same_name_calls_on_one_line_remain_distinct_and_ambiguous():
    index=bind(*fixture('class X { void f() { send(() -> work()); send(value); } }',1,1))
    calls=[c for c in index.view()['sources']['evidence-one']['calls'] if c['name']=='send']
    assert len(calls)==2 and calls[0]['call_id']!=calls[1]['call_id']
    assert audit_bound_output(output(),index)['status']=='needs_review'


@pytest.mark.parametrize('source',[
    r'class X { void f() { send(\u0061); } }',
    'class X { void f() { send(value); }',
])
def test_unavailable_whole_blob_context_never_creates_positive_syntax(source):
    index=bind(*fixture(source,1,1))
    assert index.view()['sources']['evidence-one']['status']=='lexical_context_unavailable'
    assert not index.view()['sources']['evidence-one']['calls']


def test_serialized_view_or_manual_window_cannot_mint_authority():
    index=bind(*fixture());view=index.view();saved=index.digest
    view['sources'].clear()
    assert index.digest==saved and index.view()['sources']
    with pytest.raises(Rejected):BoundIndex(view)
    with pytest.raises(Rejected):audit_bound_output(output(),view)
    with pytest.raises(Rejected):index._data='fake'
    with pytest.raises(Rejected):audit_bound_output(output(),object.__new__(BoundIndex))


@pytest.mark.parametrize('source',[
    'class X { void f() { String s="a\u2028b"; send(value); } }',
    'class X {\r\n void f() { send(value); }\r\n}',
])
def test_git_lf_and_crlf_line_semantics_match_the_existing_tool(source):
    start=2 if '\r\n' in source else 1
    index=bind(*fixture(source,start,start))
    assert any(c['name']=='send' and c['start_line']==start for c in index.view()['sources']['evidence-one']['calls'])


def test_nul_blob_is_rejected_before_lexical_proof():
    with pytest.raises(Rejected):bind(*fixture('class X { void f() { send(1); } }\0',1,1))


def test_unrecognized_java_token_does_not_create_callable_support():
    index=bind(*fixture('class X { void f() { ` send(() -> work()); } }',1,1))
    assert not index.view()['sources']['evidence-one']['calls']


def test_model_projection_is_bounded_data_and_never_a_rehydratable_authority():
    index=bind(*fixture());projected=model_view(index)
    assert len(projected['calls'])==2 and projected['publication_approval'] is False
    assert 'outside' not in json.dumps(projected) and 'after' not in json.dumps(projected)
    with pytest.raises(Rejected):model_view(index,max_chars=1)
    with pytest.raises(Rejected):model_view(index,max_chars=True)
    with pytest.raises(Rejected):audit_bound_output(output(),projected)


def test_compact_projection_keeps_every_call_and_its_exact_evidence_binding():
    index=bind(*fixture());projected=model_view(index);data=index.view()
    rows=[dict(zip(projected['call_columns'],r,strict=True)) for r in projected['calls']]
    original=[c for source in data['sources'].values() for c in source['calls']]
    assert len(rows)==len(original)
    for row,call in zip(rows,original,strict=True):
        assert row['call_id']==call['call_id'] and row['callee']==call['callee']
        assert projected['evidence_ids'][row['evidence_index']]==call['evidence_id']
    size=len(json.dumps(projected,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    with pytest.raises(Rejected):model_view(index,max_chars=size-1)


@pytest.mark.parametrize('case',['wrong_target','path','bad_seal','limit','secret'])
def test_host_policy_boundaries_are_not_relaxed(case):
    scope,refs,chunks,blobs=fixture()
    if case=='wrong_target':scope=replace(scope,topic='other')
    if case=='path':scope=replace(scope,files=(replace(scope.files[0],path='../private.java'),))
    if case=='bad_seal':scope=replace(scope,files=(replace(scope.files[0],sha256='0'*64),))
    if case=='limit':scope=replace(scope,files=scope.files*17)
    if case=='secret':scope,refs,chunks,blobs=fixture('class X { String password = "synthetic-not-a-real-secret"; }',1,1)
    with pytest.raises(Rejected):bind(scope,refs,chunks,blobs)
