from copy import deepcopy
from hashlib import sha1,sha256
from dataclasses import replace
import pytest
from project_evidence.section_review import AcceptedReads
from project_evidence.call_binding import HostScope,FileSeal
from project_evidence.source_ranges import RangedSourceTools
from project_evidence.source_policy import SourceError
from project_evidence.source_coverage import build,projection,context_with_projection,BoundCoverage,FAMILIES
from test_verified_read_observer import Source
PATH='src/Example.java'
TEXT='''class Example {
@Insert("""INSERT INTO task (id,state) VALUES (#{id}, 'WAIT')""")
void write();
void f() { if (count() >= 7) { throw new IllegalStateException("busy"); } }
}'''

def collected(accept=True):
 raw=TEXT.encode();seal=FileSeal(PATH,sha256(raw).hexdigest(),sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),((1,5),))
 scope=HostScope('current-task','project','https://github.com/example/public-java','a'*40,'submission','entry',(seal,))
 c=AcceptedReads(scope);src=Source(raw);tools=RangedSourceTools(src,{PATH:(1,5)})
 r=c.read(tools,dict(path=PATH,start_line=1,end_line=5));assert r['status']=='ok'
 saved=dict(r,id='e1');c.saved(r,saved)
 if accept:c.accept(dict(type='tool_feedback_accepted',details=dict(tool='read_excerpt',status='ok',evidence_id='e1')))
 ref={k:r[k] for k in ('path','public_url','commit','blob_oid','sha256','start_line','end_line','truncated')};ref['evidence_id']='e1'
 ctx=dict(commit=scope.commit,public_url=scope.public_url,goal='section_explanation',anchor=dict(topic='submission',section_id='entry'),observations=[dict(name='read_excerpt',result=dict(r,evidence_id='e1'))],history=[dict(unrelated='preserved')])
 return c,[ref],ctx


def test_current_tool_bytes_saved_and_accepted_are_bound_without_new_source_reads():
 c,refs,ctx=collected();b=build(c,refs,tuple(FAMILIES));v=projection(b,c,refs)
 assert v['facts'] and v['calls'] and not v['publication_approval']
 pairs=[r for r in v['facts'] if r[4]=='task.state'];assert len(pairs)==1 and pairs[0][5]=="'WAIT'"
 assert len(c._blobs)==1


@pytest.mark.parametrize('case',['json','foreign_collector','modified_ref','modified_blob','unaccepted','frozen'])
def test_serialized_wrong_task_or_unaccepted_material_cannot_be_used(case):
 c,refs,ctx=collected(accept=case!='unaccepted')
 if case=='unaccepted':
  with pytest.raises((ValueError,SourceError)):build(c,refs,tuple(FAMILIES))
  return
 b=build(c,refs,tuple(FAMILIES))
 if case=='json':b=b.view()
 if case=='foreign_collector':c=collected()[0]
 if case=='modified_ref':refs[0]['commit']='b'*40
 if case=='modified_blob':c._blobs[PATH]+=b'\n'
 if case=='frozen':c._frozen=True
 with pytest.raises((ValueError,SourceError)):projection(b,c,refs)


def test_selected_groups_partition_items_and_keep_omission_explicit():
 c,r,_=collected();b=build(c,r,('writes',));v=projection(b,c,r)
 assert all(v['kind_codes'][f[0]]=='insert_field' for f in v['facts'])
 assert v['omitted_by_host_selection']>0 and not v['semantic_verified']
 data=b.view();data['groups'].clear();assert b.view()['groups']
 with pytest.raises(ValueError):BoundCoverage(data,c,'fake')
 with pytest.raises(ValueError):b._data='changed'


@pytest.mark.parametrize('facets',[(),('unknown',),('writes','writes'),['writes']])
def test_only_explicit_host_facet_configuration_is_accepted(facets):
 c,r,_=collected()
 with pytest.raises(ValueError):build(c,r,facets)


def test_combined_projection_replaces_only_matching_call_entry_preserves_source_and_history():
 c,r,ctx=collected();ctx['history'].append(c.model_context(r));before=deepcopy(ctx)
 v=context_with_projection(ctx,build(c,r,tuple(FAMILIES)),c,r)
 assert ctx==before and v['observations']==before['observations'] and v['history'][0]==before['history'][0]
 assert len(v['history'])==2 and 'host_source_coverage' in v['history'][-1]
 assert len(v['history'][-1]['host_source_coverage']['calls'])==len(before['history'][-1]['host_source_call_evidence']['calls'])


@pytest.mark.parametrize('case',['commit','excerpt','missing','foreign_old','mixed_old','duplicate'])
def test_context_tampering_does_not_gain_a_coverage_view(case):
 c,r,ctx=collected();b=build(c,r,tuple(FAMILIES));ctx['history'].append(c.model_context(r))
 if case=='commit':ctx['commit']='b'*40
 if case=='excerpt':ctx['observations'][0]['result']['excerpt']='changed'
 if case=='missing':ctx['observations']=[]
 if case=='foreign_old':ctx['history'][-1]['host_source_call_evidence']['index_digest']='other'
 if case=='mixed_old':ctx['history'][-1]['business_data']='must not discard'
 if case=='duplicate':ctx['history'].append(ctx['history'][-1])
 with pytest.raises(ValueError):context_with_projection(ctx,b,c,r)


def test_projection_and_entire_context_limits_fail_before_any_dispatch_without_truncation():
 c,r,ctx=collected();b=build(c,r,tuple(FAMILIES));before=b.view()
 with pytest.raises(SourceError):projection(b,c,r,max_chars=1)
 ctx['metadata']='x'*24000
 with pytest.raises(SourceError):context_with_projection(ctx,b,c,r)
 assert b.view()==before and len(projection(b,c,r)['facts'])==len(before['inventory']['facts'])

@pytest.mark.parametrize('prefix,suffix',[('/*\n','\n*/'),('String s="""\n','\n""";')])
def test_prefix_lexical_context_prevents_fake_annotation_inside_comment_or_string(prefix,suffix):
 text=prefix+'@Insert("""INSERT INTO forged (x) VALUES (1)""")'+suffix
 # Text-block case uses a fake one-line constant/throw instead of nesting a
 # delimiter, which would really terminate the enclosing Java text block.
 if prefix.startswith('String'):text=prefix+'final int FAKE=9; throw new Fake();'+suffix
 raw=text.encode();seal=FileSeal(PATH,sha256(raw).hexdigest(),sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),((2,2),))
 c=AcceptedReads(HostScope('lexical','project','https://github.com/example/public-java','a'*40,'submission','entry',(seal,)))
 result=c.read(RangedSourceTools(Source(raw),{PATH:(2,2)}),dict(path=PATH,start_line=2,end_line=2));saved=dict(result,id='e1');c.saved(result,saved)
 c.accept(dict(type='tool_feedback_accepted',details=dict(tool='read_excerpt',status='ok',evidence_id='e1')))
 refs=[c._accepted['e1'][0]]
 assert build(c,refs,tuple(FAMILIES)).view()['inventory']['facts']==[]

@pytest.mark.parametrize('case',['raise_projection_cap','tamper_old_call','contradictory_text','foreign_task_scope'])
def test_extra_identity_and_size_boundaries_remain_closed(case):
 c,r,ctx=collected();b=build(c,r,tuple(FAMILIES));ctx['history'].append(c.model_context(r))
 if case=='raise_projection_cap':
  with pytest.raises(SourceError):projection(b,c,r,max_chars=8193)
  return
 if case=='tamper_old_call':ctx['history'][-1]['host_source_call_evidence']['calls'][0][2]='fake'
 if case=='contradictory_text':ctx['observations'][0]['result']['excerpt']=ctx['observations'][0]['result']['text'];ctx['observations'][0]['result']['text']='different'
 if case=='foreign_task_scope':c.scope=replace(c.scope,task_id='other-task')
 with pytest.raises((ValueError,SourceError)):context_with_projection(ctx,b,c,r)


def test_complete_statement_cannot_be_stitched_from_two_separate_admitted_fragments():
 text='class Example {\n@Insert("""\nINSERT INTO task (state) VALUES (\'WAIT\')\n""") void write();\n}'
 raw=text.encode();seal=FileSeal(PATH,sha256(raw).hexdigest(),sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),((2,2),(4,4)))
 c=AcceptedReads(HostScope('gaps','project','https://github.com/example/public-java','a'*40,'submission','entry',(seal,)))
 tools=RangedSourceTools(Source(raw),{PATH:((2,2),(4,4))});refs=[]
 for line in (2,4):
  result=c.read(tools,dict(path=PATH,start_line=line,end_line=line));eid=f'e{line}';c.saved(result,dict(result,id=eid))
  c.accept(dict(type='tool_feedback_accepted',details=dict(tool='read_excerpt',status='ok',evidence_id=eid)));refs.append(c._accepted[eid][0])
 assert build(c,refs,tuple(FAMILIES)).view()['inventory']['facts']==[]
