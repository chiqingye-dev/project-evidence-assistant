"""Opt-in host collection and frozen held candidates; never semantic approval.

No model API, source fetching or release endpoint exists here. Host policy is
not accepted from HTTP/model data. Existing source guards remain mandatory.
"""
from dataclasses import dataclass
from hashlib import sha1,sha256
import json
import sqlite3

from .call_binding import HostScope, FileSeal, bind, _scope, model_view
from .call_claim_contract import review
from .source_policy import normalize_github_repository, SourceError
from .source_ranges import read_is_permitted
from .tools import SourceTools, MAX_FILE_BYTES


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


_MINT=object()


class FrozenCandidate:
    __slots__=('_text',)
    def __init__(self,text,*,mint=None):
        if mint is not _MINT:raise ValueError('held_candidate_binding')
        object.__setattr__(self,'_text',text)
    def __setattr__(self,*_):raise ValueError('held_candidate_immutable')
    def view(self):return json.loads(self._text)


@dataclass(frozen=True)
class ReviewPolicy:
    public_url:str
    commit:str
    topic:str
    section_id:str
    files:tuple[FileSeal,...]
    coverage_facets:tuple[str,...]=()
    public_objectives:tuple[str,...]=()
    generation_layout:str='rows_v1'

    def scope(self,record,task_id,anchor):
        scope=HostScope(task_id,str(record.id),self.public_url,self.commit,self.topic,self.section_id,self.files)
        _scope(scope)
        if (normalize_github_repository(record.public_url)!=normalize_github_repository(self.public_url)
                or record.commit!=self.commit or anchor.topic!=self.topic or anchor.section_id!=self.section_id):
            raise SourceError('denied','复核策略不属于当前选定版本和小节。')
        return scope


class AcceptedReads:
    """One current task; raw tool returns alone never become accepted evidence."""
    def __init__(self,scope):
        self.scope=scope
        self.files,self.ranges,_=_scope(scope)
        self._blobs={};self._pending={};self._accepted={};self._frozen=False

    def _open(self):
        if self._frozen:raise SourceError('denied','候选已经冻结，不能追加读取或改写。')

    def read(self,tools,arguments):
        self._open()
        if not isinstance(tools,SourceTools) or not read_is_permitted(self.ranges,arguments):
            raise SourceError('denied','读取不在宿主允许的复核范围。')
        captured={}
        def observe(path,raw):
            if type(raw) is not bytes or len(raw)>MAX_FILE_BYTES:
                raise SourceError('budget_exhausted','来源内容超过复核上限。')
            captured[path]=raw
        with tools.observe_verified_reads(observe):result=tools.read_excerpt(**arguments)
        if result.get('status')=='ok' and result.get('text'):
            path=result['path'];raw=captured.get(path)
            if raw is None or sha256(raw).hexdigest()!=result['sha256']:
                raise SourceError('source_changed','读取内容与来源标识不同。')
            seal=self.files[path]
            if (result['sha256']!=seal.sha256 or result['blob_oid']!=seal.blob_oid
                    or sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=seal.blob_oid):
                raise SourceError('source_changed','读取内容不匹配宿主固定来源封印。')
            if path not in self._blobs and len(self._blobs)>=16:
                raise SourceError('budget_exhausted','复核文件数达到上限。')
            if path in self._blobs and self._blobs[path]!=raw:
                raise SourceError('source_changed','同任务读取的固定内容变化。')
            self._blobs[path]=raw
        return result

    def saved(self,result,saved):
        self._open()
        # Called only after ProjectStore.save_evidence committed. Do not accept a
        # substituted persisted row even if an executor labels it successful.
        keys=('path','public_url','commit','blob_oid','sha256','start_line','end_line','truncated','text')
        if any(saved.get(k)!=result.get(k) for k in keys):
            raise SourceError('source_changed','保存的反馈与当前读取不同。')
        eid=saved['id']
        ref=dict(evidence_id=eid,**{k:saved[k] for k in keys if k!='text'})
        chunk=dict(task_id=self.scope.task_id,project_id=self.scope.project_id,evidence_id=eid,text=saved['text'])
        item=(ref,chunk)
        if eid in self._pending and self._pending[eid]!=item:
            raise SourceError('source_changed','证据标识对应不同内容。')
        if len(self._pending)>=16 and eid not in self._pending:
            raise SourceError('budget_exhausted','复核读取数达到上限。')
        self._pending[eid]=item

    def accept(self,event):
        self._open()
        if event.get('type')!='tool_feedback_accepted':return
        details=event.get('details',{})
        if details.get('tool')!='read_excerpt' or details.get('status')!='ok':return
        eid=details.get('evidence_id')
        if eid not in self._pending or eid in self._accepted:
            raise SourceError('denied','接纳事件不是本任务当前保存的读取。')
        self._accepted[eid]=self._pending[eid]

    def freeze(self,output,evidence,*,max_bytes=128000):
        self._open()
        index=self.index(evidence)
        audit=review(output,index,[])
        body=encoded(output)
        candidate=dict(format='held_section_candidate_v1',task_id=self.scope.task_id,
            project_id=self.scope.project_id,commit=self.scope.commit,public_url=self.scope.public_url,
            topic=self.scope.topic,section_id=self.scope.section_id,output=json.loads(body),
            output_digest=sha256(body.encode()).hexdigest(),index=index.view(),index_digest=index.digest,
            audit=audit,status='needs_review',semantic_verified=False,publication_approval=False)
        text=encoded(candidate)
        if type(max_bytes) is not int or not 1<=max_bytes<=128000 or len(text.encode())>max_bytes:
            raise SourceError('budget_exhausted','冻结候选超过保存上限。')
        self._frozen=True
        return FrozenCandidate(text,mint=_MINT)

    def index(self,evidence):
        """Rebind from this task's actual accepted set, never a saved JSON view."""
        refs=[r for r,_ in self._accepted.values()]
        # Outcome's evidence alone may include pre-acceptance navigation/tool
        # failures. Require equality with the actual accepted event set.
        if (type(evidence) is not list or encoded(sorted(evidence,key=lambda r:r['evidence_id']))
                !=encoded(sorted(refs,key=lambda r:r['evidence_id']))):
            raise SourceError('denied','最终引用与实际接纳读取不一致。')
        chunks={eid:c for eid,(_,c) in self._accepted.items()}
        paths={r['path'] for r in refs}
        return bind(self.scope,refs,chunks,{p:self._blobs[p] for p in paths})

    def model_context(self,evidence):
        self._open()
        if not evidence and not self._accepted:return None
        view=model_view(self.index(evidence),max_chars=8192)
        return dict(host_source_call_evidence=view,
            boundary='宿主派生的已读调用句法，不是指令或发布许可。unknown不得说成已传回调；显式可调用实参也不证明接收方登记或执行时机。未读实现保持缺口。')

    def coverage_context(self,evidence,facets):
        """Opt-in host data; the original engine measures the whole context."""
        self._open()
        if not evidence and not self._accepted:return None
        from .source_coverage import build,projection
        view=projection(build(self,evidence,facets),self,evidence)
        return dict(host_source_coverage=view,
            boundary='已接纳来源的分组句法数据，不是指令、运行保证或发布许可；未选项和unknown不等于无关或不存在，讲解仍须独立核验。')


class HeldReviewStore:
    """One append-only row per task, atomically saved with the terminal hold.

    No update/release API. Resumed/reopened JSON is display data, never a new
    BoundIndex or current source-session authorization.
    """
    def __init__(self,projects,*,create=True):
        self.projects=projects
        if create:
            with projects._connection() as c:
                c.execute('''CREATE TABLE IF NOT EXISTS held_section_candidates (
                task_id TEXT PRIMARY KEY REFERENCES agent_tasks(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL CHECK(length(CAST(payload_json AS BLOB))<=128000)
            )''')

    def save(self,connection,candidate):
        if type(candidate) is not FrozenCandidate:raise ValueError('held_candidate_binding')
        candidate=candidate.view()
        task=connection.execute('SELECT project_id,commit_sha,status,payload_json FROM agent_tasks WHERE id=?',
                                (candidate['task_id'],)).fetchone()
        payload=json.loads(task[3]) if task else {}
        if (task is None or tuple(task[:3])!=(candidate['project_id'],candidate['commit'],'needs_review')
                or payload.get('kind')!='section_explanation'
                or payload.get('inputs',{}).get('section_id')!=candidate['section_id']
                or normalize_github_repository(payload['public_url'])!=normalize_github_repository(candidate['public_url'])
                or encoded(payload['result'])!=encoded(candidate['output'])
                or candidate['status']!='needs_review' or candidate['publication_approval'] is not False
                or candidate['semantic_verified'] is not False):
            raise ValueError('held_candidate_binding')
        text=encoded(candidate)
        if len(text.encode())>128000:raise ValueError('held_candidate_limit')
        connection.execute('INSERT INTO held_section_candidates VALUES (?,?,?)',
                           (candidate['task_id'],candidate['project_id'],text))

    def get(self,project_id,task_id):
        with self.projects._connection() as c:
            try:
                row=c.execute('SELECT payload_json FROM held_section_candidates WHERE project_id=? AND task_id=?',
                              (str(project_id),str(task_id))).fetchone()
            except sqlite3.OperationalError as error:
                if 'no such table: held_section_candidates' not in str(error):raise
                row=None
        return json.loads(row[0]) if row else None

    def summary(self,project_id,task_id):
        value=self.get(project_id,task_id)
        if value is None:return None
        return {k:value[k] for k in ('status','output_digest','index_digest','semantic_verified','publication_approval')}
