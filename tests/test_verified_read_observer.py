"""Retain real ranged-tool overrides while observing already-verified bytes."""
from hashlib import sha1,sha256
import pytest
from project_evidence.section_review import AcceptedReads
from project_evidence.source_ranges import RangedSourceTools
from project_evidence.source_policy import SourceError
from project_evidence.tools import SourceTools
from test_call_binding import fixture,PATH,SOURCE


class Source:
    commit='a'*40
    public_url='https://github.com/example/public-java'
    def __init__(self,raw=SOURCE.encode()):self.raw=raw;self.reads=[]
    def entries(self):return [dict(path=PATH,size=len(self.raw),blob_oid=sha1(b'blob '+str(len(self.raw)).encode()+b'\0'+self.raw).hexdigest())]
    def status(self):return dict(commit=self.commit,head_commit=self.commit,working_tree_changed=False)
    def read_bytes(self,path):self.reads.append(path);return self.raw


class CheckedRanges(RangedSourceTools):
    """Same override pattern used by the sealed real S03 source wrapper."""
    def __init__(self,source,expected):
        super().__init__(source,{PATH:((4,4),)});self.expected=expected;self.guarded_reads=0
    def _read(self,entry,deadline):
        self.guarded_reads+=1
        result=super()._read(entry,deadline)
        if result[2]!=self.expected:raise SourceError('source_changed','fixed card drift')
        return result


def test_collector_preserves_scoped_subclass_guards_and_reads_only_once():
    scope,refs,chunks,blobs=fixture();source=Source();tools=CheckedRanges(source,scope.files[0].sha256)
    collector=AcceptedReads(scope)
    result=collector.read(tools,dict(path=PATH,start_line=4,end_line=4))
    assert result['status']=='ok' and tools.guarded_reads==1 and source.reads==[PATH]
    assert tools.source is source and tools._verified_read_observer is None
    saved=dict(result,id=refs[0]['evidence_id']);collector.saved(result,saved)
    collector.accept(dict(type='tool_feedback_accepted',details=dict(tool='read_excerpt',status='ok',evidence_id=saved['id'])))
    view=collector.model_context(refs)['host_source_call_evidence']
    assert {c[2] for c in view['calls']}=={'send','work'}
    assert tools.search_text('outside')['status']=='no_results'
    assert source.reads==[PATH,PATH] and tools._verified_read_observer is None


def test_more_restrictive_subclass_range_is_not_replaced_by_plain_source_tools():
    scope,*_=fixture();source=Source();tools=CheckedRanges(source,scope.files[0].sha256)
    result=AcceptedReads(scope).read(tools,dict(path=PATH,start_line=3,end_line=3))
    assert result['status']=='denied' and source.reads==[] and tools.guarded_reads==0


def test_scoped_digest_failure_still_denies_feedback_even_after_observation():
    scope,*_=fixture();source=Source();tools=CheckedRanges(source,'0'*64);collector=AcceptedReads(scope)
    result=collector.read(tools,dict(path=PATH,start_line=4,end_line=4))
    assert result['status']=='source_changed' and not collector._blobs and not collector._pending
    assert tools._verified_read_observer is None and source.reads==[PATH]


@pytest.mark.parametrize('raw',[b'class X {\0}',b'\xff',b'x'*262145,b'class X { String apiKey = "not-a-placeholder-live-like-fixture-value"; }'],
    ids=['binary','encoding','size','sensitive'])
def test_invalid_or_sensitive_bytes_never_reach_the_host_observer(raw):
    source=Source(raw);tools=SourceTools(source);observed=[]
    with tools.observe_verified_reads(lambda *x:observed.append(x)):
        result=tools.read_excerpt(PATH,1,1)
    assert result['status']!='ok' and observed==[] and tools._verified_read_observer is None


def test_existing_observer_is_chained_and_restored_even_on_failure():
    source=Source();tools=SourceTools(source);seen=[]
    first=lambda path,raw:seen.append(('first',path))
    tools._verified_read_observer=first
    def reject(path,raw):seen.append(('second',path));raise SourceError('denied','synthetic hook stop')
    with tools.observe_verified_reads(reject):assert tools.read_excerpt(PATH,4,4)['status']=='denied'
    assert seen==[('first',PATH),('second',PATH)] and tools._verified_read_observer is first
    assert source.reads==[PATH]


def test_subclass_bypassing_verified_base_read_cannot_mint_a_collector_blob():
    class Bypass(SourceTools):
        def _read(self,entry,deadline):return SOURCE,SOURCE.splitlines(),sha256(SOURCE.encode()).hexdigest()
    scope,*_=fixture();tools=Bypass(Source());collector=AcceptedReads(scope)
    with pytest.raises(SourceError,match='读取内容与来源标识不同'):collector.read(tools,dict(path=PATH,start_line=4,end_line=4))
    assert tools._verified_read_observer is None and not collector._blobs
