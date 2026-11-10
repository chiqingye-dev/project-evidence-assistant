"""Host-only binding of already-accepted reads to immutable blob call syntax.

No I/O, network, model, publication or source authorization. The host must supply
scope seals from its independently verified source session and accepted reads
from its engine, never from model-authored JSON. Whole already-readable blobs
are lexed for context; only calls fully inside an admitted read are exported.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha1,sha256
import json
import re

from . import call_evidence as syntax
from .source_policy import normalize_github_repository,require_allowed_path,SourceError
from .source_ranges import normalize_read_ranges,read_is_permitted
from .tools import MAX_FILE_BYTES,contains_sensitive_content,_lines

FORMAT='bound_call_evidence_v1'
_MINT=object()
_REF_KEYS={'evidence_id','path','public_url','commit','blob_oid','sha256','start_line','end_line','truncated'}
_CHUNK_KEYS={'task_id','project_id','evidence_id','text'}
_REASONS={'host_scope','accepted_reads','chunk_binding','source_identity','source_bytes','source_span','source_text','limits','bound_index'}


class Rejected(ValueError):
    def __init__(self,reason):super().__init__(reason if reason in _REASONS else 'bound_index')


@dataclass(frozen=True)
class FileSeal:
    path:str
    sha256:str
    blob_oid:str
    spans:tuple


@dataclass(frozen=True)
class HostScope:
    task_id:str
    project_id:str
    public_url:str
    commit:str
    topic:str
    section_id:str
    files:tuple


def _encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def _digest(value):return sha256(_encoded(value).encode()).hexdigest()
def _identifier(value):return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value) is not None
def _hex(value,length):return isinstance(value,str) and re.fullmatch('[0-9a-f]{'+str(length)+'}',value) is not None


class BoundIndex:
    """Opaque process-local result, not reconstructible from a model JSON view.

    This is an API boundary, not a sandbox against arbitrary Python code in the
    trusted host. A serialization is data only and is never accepted as proof.
    """
    __slots__=('_data','_mint')
    def __init__(self,data,*,_mint=None):
        if _mint is not _MINT:raise Rejected('bound_index')
        object.__setattr__(self,'_data',_encoded(data));object.__setattr__(self,'_mint',_mint)
    def __setattr__(self,*_):raise Rejected('bound_index')
    @property
    def digest(self):return sha256(self._data.encode()).hexdigest()
    def view(self):return json.loads(self._data)


def _checked(index):
    if type(index) is not BoundIndex or getattr(index,'_mint',None) is not _MINT:raise Rejected('bound_index')
    return index.view()


def _scope(scope):
    try:
        if type(scope) is not HostScope or not all(_identifier(v) for v in (scope.task_id,scope.project_id,scope.section_id)):
            raise ValueError
        if scope.topic not in ('query','submission','consumption','judging') or not _hex(scope.commit,40):raise ValueError
        if not isinstance(scope.public_url,str):raise ValueError
        repository=normalize_github_repository(scope.public_url)
        if type(scope.files) is not tuple or not 1<=len(scope.files)<=16:raise ValueError
        files={};ranges={}
        for seal in scope.files:
            if type(seal) is not FileSeal or seal.path in files:raise ValueError
            require_allowed_path(seal.path)
            if not seal.path.endswith('.java') or not _hex(seal.sha256,64) or not _hex(seal.blob_oid,40):raise ValueError
            if type(seal.spans) is not tuple:raise ValueError
            files[seal.path]=seal;ranges[seal.path]=seal.spans
        ranges=normalize_read_ranges(ranges)
        return files,ranges,repository
    except (ValueError,TypeError,SourceError):raise Rejected('host_scope') from None


def bind(scope,accepted_refs,chunks,full_blobs):
    """Bind host-selected scope + actual engine reads + exact saved excerpts.

    Full bytes supply hash/lexical context, not additional model-visible facts.
    No manual lexical-context flag is accepted. Unsupported lexing yields an
    index with unavailable syntax, never positive callback evidence.
    """
    files,ranges,repository=_scope(scope)
    if (type(accepted_refs) is not list or not 1<=len(accepted_refs)<=16 or
            type(chunks) is not dict or type(full_blobs) is not dict):raise Rejected('accepted_reads')
    refs={};paths=set()
    for ref in accepted_refs:
        if type(ref) is not dict or set(ref)!=_REF_KEYS or not _identifier(ref.get('evidence_id')):raise Rejected('accepted_reads')
        identifier=ref['evidence_id']
        if identifier in refs:raise Rejected('accepted_reads')
        if not isinstance(ref['path'],str) or not isinstance(ref['public_url'],str):raise Rejected('source_identity')
        seal=files.get(ref['path'])
        try:repo=normalize_github_repository(ref['public_url'])
        except (ValueError,TypeError,SourceError):raise Rejected('source_identity') from None
        if (seal is None or ref['commit']!=scope.commit or repo!=repository or ref['sha256']!=seal.sha256
                or ref['blob_oid']!=seal.blob_oid or ref['truncated'] is not False):raise Rejected('source_identity')
        if not read_is_permitted(ranges,ref):raise Rejected('source_span')
        refs[identifier]=deepcopy(ref);paths.add(ref['path'])
    if set(chunks)!=set(refs) or set(full_blobs)!=paths:raise Rejected('chunk_binding')
    decoded={};tokenized={};contexts={}
    for path in sorted(paths):
        raw=full_blobs[path];seal=files[path]
        if type(raw) is not bytes or not 1<=len(raw)<=MAX_FILE_BYTES or b'\0' in raw:raise Rejected('source_bytes')
        if sha256(raw).hexdigest()!=seal.sha256 or sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=seal.blob_oid:
            raise Rejected('source_bytes')
        try:text=raw.decode('utf-8')
        except UnicodeError:raise Rejected('source_bytes') from None
        if contains_sensitive_content(text):raise Rejected('source_text')
        decoded[path]=_lines(text)
        try:
            tokens=syntax._tokens(text);pairs=syntax._paired(tokens)
            tokenized[path]=(tokens,pairs);contexts[path]='whole_blob_lexical_context'
        except syntax.Unsupported:
            tokenized[path]=None;contexts[path]='lexical_context_unavailable'
    sources={};read_bindings=[]
    for identifier,ref in refs.items():
        chunk=chunks[identifier]
        if (type(chunk) is not dict or set(chunk)!=_CHUNK_KEYS or chunk['task_id']!=scope.task_id
                or chunk['project_id']!=scope.project_id or chunk['evidence_id']!=identifier
                or not isinstance(chunk['text'],str)):raise Rejected('chunk_binding')
        lines=decoded[ref['path']];a,b=ref['start_line'],ref['end_line']
        if b>len(lines) or chunk['text']!='\n'.join(lines[a-1:b]) or len(chunk['text'])>12000:
            raise Rejected('source_text')
        values=[];status=contexts[ref['path']]
        if tokenized[ref['path']] is not None:
            tokens,pairs=tokenized[ref['path']]
            # Extraction filters before constructing labels and exports no names
            # or calls outside the particular accepted span.
            window=syntax.Window(identifier,ref['path'],1,'')
            try:values=syntax._extract_calls(tokens,pairs,window,(a,b))
            except (syntax.Unsupported,ValueError):status='call_syntax_unavailable';values=[]
        for value in values:
            value['call_id']=_digest([FORMAT,scope.task_id,identifier,ref['sha256'],value])[:32]
        sources[identifier]={'status':status,'calls':values}
        read_bindings.append(dict(ref,excerpt_sha256=sha256(chunk['text'].encode()).hexdigest()))
    if sum(len(s['calls']) for s in sources.values())>128:raise Rejected('limits')
    data=dict(format=FORMAT,task_id=scope.task_id,project_id=scope.project_id,public_url=repository,
        commit=scope.commit,topic=scope.topic,section_id=scope.section_id,
        scope=[dict(path=p,sha256=f.sha256,blob_oid=f.blob_oid,spans=ranges[p]) for p,f in sorted(files.items())],
        accepted_reads=read_bindings,sources=sources,publication_approval=False,semantic_verified=False)
    return BoundIndex(data,_mint=_MINT)


def audit_bound_output(output,index):
    """Only factory-bound sources, never a model-provided window/context flag."""
    data=_checked(index)
    if type(output) is not dict or output.get('topic')!=data['topic'] or output.get('section_id')!=data['section_id']:
        raise Rejected('source_identity')
    result=syntax._audit_output_sources(output,data['sources'])
    return dict(result,index_digest=index.digest,source_binding='automatic_actual_read_and_blob_context',
        semantic_review_required=True)


def model_view(index,*,max_chars=8192):
    """All-or-nothing data projection; caller must budget it with model context.

    Serialization is not authority. No truncation, hidden omitted evidence or
    implicit budget increase. The host must stop before dispatch on overflow.
    """
    data=_checked(index)
    if type(max_chars) is not int or not 1<=max_chars<=24000:raise Rejected('limits')
    identifiers=list(data['sources'])
    view=dict(format=FORMAT,representation='call_rows_v1',index_digest=index.digest,topic=data['topic'],section_id=data['section_id'],
        scope='call argument syntax only; unknown is not absence; receiver behavior unverified',
        evidence_ids=identifiers,evidence_index_base=0,
        call_columns=['call_id','evidence_index','callee','start_line','end_line','passing'],
        calls=[[c['call_id'],identifiers.index(c['evidence_id']),c['callee'],c['start_line'],c['end_line'],c['passing']]
               for source in data['sources'].values() for c in source['calls']],
        unavailable_evidence=[identifier for identifier,source in data['sources'].items()
                              if source['status']!='whole_blob_lexical_context'],
        publication_approval=False,semantic_verified=False)
    if len(_encoded(view))>max_chars:raise Rejected('limits')
    return view
