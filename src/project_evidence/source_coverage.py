"""Host-only grouped accepted-source syntax projection prototype.
No I/O, source refresh, model tool, publication, generation or semantic approval.
"""
from copy import deepcopy
from hashlib import sha256
import json,re
from .source_coverage_inventory import inventory,encode,digest,split_items
from project_evidence.section_review import AcceptedReads
from project_evidence.tools import _lines
from project_evidence.source_policy import SourceError

FAMILIES={'branches':{'constant','condition','exception','transaction_declaration'},'queries':{'count_term','filter_term','lock_clause'},'snapshot_fields':{'projection_field'},'writes':{'insert_field'},'write_inputs':{'insert_field'},'payload_response':{'payload_field','return_value'}}
_MINT=object()
class BoundCoverage:
    __slots__=('_data','_collector','_index_digest','_mint')
    def __init__(self,data,collector,index_digest,*,mint=None):
        if mint is not _MINT:raise ValueError('coverage_authority')
        object.__setattr__(self,'_data',encode(data));object.__setattr__(self,'_collector',collector)
        object.__setattr__(self,'_index_digest',index_digest);object.__setattr__(self,'_mint',mint)
    def __setattr__(self,*_):raise ValueError('coverage_immutable')
    def view(self):return json.loads(self._data)

def build(collector,evidence,facets):
    if type(collector) is not AcceptedReads:raise ValueError('coverage_collector')
    collector._open();idx=collector.index(evidence) # Existing bytes, hash, scope and accepted-feedback checks.
    if type(facets) is not tuple or not facets or len(set(facets))!=len(facets) or any(x not in FAMILIES for x in facets):raise ValueError('host_facets')
    # Parse verified complete blobs for lexical context, then export only facts
    # fully contained in EACH actual accepted read; never stitch across gaps.
    combined=[];unsupported=[]
    for ref,chunk in collector._accepted.values():
        lines=_lines(collector._blobs[ref['path']].decode('utf-8'))
        text='\n'.join(lines)
        read=dict(ref,text=text,start_line=1,end_line=len(lines),excerpt_sha256=sha256(text.encode()).hexdigest())
        parsed=inventory([read])
        combined.extend(f for f in parsed['facts'] if ref['start_line']<=f['start_line']<=f['end_line']<=ref['end_line'])
        unsupported.extend(parsed['unsupported'])
        if len(combined)>256:raise SourceError('budget_exhausted','覆盖条目达到上限。')
    inv=dict(format='whole_blob_context_admitted_facts_v1',facts=combined,unsupported=unsupported,
        read_ids=list(collector._accepted),semantic_verified=False,publication_approval=False)
    inv['digest']=digest(inv)
    if len(encode(inv).encode())>128000:raise SourceError('budget_exhausted','覆盖清单超过宿主上限。')
    groups={}
    for k in facets:
        chosen=[]
        for f in inv['facts']:
            if f['kind'] not in FAMILIES[k]:continue
            simple_input=f['kind']=='insert_field' and re.fullmatch(r'#\{[^}]+\}',detail(f)) is not None
            if k=='writes' and simple_input or k=='write_inputs' and not simple_input:continue
            chosen.append(f['id'])
        groups[k]=chosen
    selected={i for values in groups.values() for i in values}
    value=dict(format='bound_coverage_inventory_v1',task_id=collector.scope.task_id,project_id=collector.scope.project_id,commit=collector.scope.commit,
        source_index_digest=idx.digest,inventory=inv,host_facets=list(facets),groups=groups,
        omitted_by_host_selection=[f['id'] for f in inv['facts'] if f['id'] not in selected],selection_is_not_required_fact_or_quality_judgement=True,
        semantic_verified=False,publication_approval=False)
    return BoundCoverage(value,collector,idx.digest,mint=_MINT)

def detail(fact):
    if fact['kind']=='insert_field':
        m=re.fullmatch(r'\s*INSERT\s+INTO\s+(\w+)\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*',fact['source_quote'],re.I|re.S)
        if not m:raise ValueError('bound_insert_shape')
        pairs=dict(zip(split_items(m[2]),split_items(m[3])))
        return pairs[fact['subject'].split('.',1)[1]]
    if fact['kind']=='payload_field':
        value=json.loads(fact['source_quote'][3:-3]);return value[fact['subject']]
    return fact['source_quote']

def projection(bound,collector,evidence,*,max_chars=8192):
    if type(bound) is not BoundCoverage or getattr(bound,'_mint',None) is not _MINT or bound._collector is not collector:raise ValueError('coverage_authority')
    collector._open();idx=collector.index(evidence)
    if idx.digest!=bound._index_digest:raise ValueError('coverage_stale')
    data=bound.view();sources=idx.view()['sources'];ids=list(sources)
    kinds=sorted({f['kind'] for f in data['inventory']['facts']})
    selected={i for values in data['groups'].values() for i in values}
    rows=[];map_indices={}
    for fact in data['inventory']['facts']:
        if fact['id'] not in selected:continue
        map_indices[fact['id']]=len(rows)
        rows.append([kinds.index(fact['kind']),ids.index(fact['evidence_id']),fact['start_line'],fact['end_line'],fact['subject'],detail(fact)])
    calls=[[ids.index(eid),c['start_line'],c['end_line'],c['callee'],c['passing']] for eid,src in sources.items() for c in src['calls']]
    view=dict(format='coverage_and_calls_rows_v1',source_digest=idx.digest,coverage_digest=digest(data),evidence_ids=ids,kind_codes=kinds,
        fact_columns=['kind','evidence','start','end','subject','source_value'],facts=rows,
        host_groups={k:[map_indices[i] for i in values] for k,values in data['groups'].items()},
        call_columns=['evidence','start','end','callee','passing'],calls=calls,
        call_source_status=[[ids.index(eid),src['status']] for eid,src in sources.items()],
        unsupported=data['inventory']['unsupported'],omitted_by_host_selection=len(data['omitted_by_host_selection']),
        notice='Selected syntax data only. Not all syntax items are mandatory facts. Unknown calls are not callback absence; receiver behavior and semantic coverage remain unverified.',
        semantic_verified=False,publication_approval=False)
    if type(max_chars) is not int or not 1<=max_chars<=8192 or len(encode(view))>max_chars:raise SourceError('budget_exhausted','覆盖视图超过上限。')
    return view

def context_with_projection(context,bound,collector,evidence,*,max_chars=24000):
    # Caller provides a detached context already checked by the original engine.
    if type(bound) is not BoundCoverage or getattr(bound,'_mint',None) is not _MINT or bound._collector is not collector:raise ValueError('coverage_authority')
    value=deepcopy(context)
    if value.get('commit')!=collector.scope.commit or value.get('public_url','').rstrip('/').lower()!=collector.scope.public_url.rstrip('/').lower():raise ValueError('context_identity')
    anchor=value.get('anchor',{})
    if value.get('goal')!='section_explanation' or anchor.get('topic')!=collector.scope.topic or anchor.get('section_id')!=collector.scope.section_id:raise ValueError('context_identity')
    observed={}
    for obs in value.get('observations',[]):
        r=obs.get('result',{})
        if obs.get('name')!='read_excerpt' or r.get('status')!='ok':continue
        eid=r.get('evidence_id')
        if eid in observed or eid not in collector._accepted:raise ValueError('context_reads')
        ref,chunk=collector._accepted[eid]
        if any(r.get(k)!=ref[k] for k in ('path','public_url','commit','sha256','blob_oid','start_line','end_line','truncated')) or not any(k in r for k in ('excerpt','text')) or any(r[k]!=chunk['text'] for k in ('excerpt','text') if k in r):raise ValueError('context_reads')
        observed[eid]=r
    if set(observed)!=set(collector._accepted):raise ValueError('context_reads')
    history=value.get('history',[])
    if any('host_source_coverage' in h for h in history):raise ValueError('duplicate_coverage')
    old=[h for h in history if 'host_source_call_evidence' in h]
    if len(old)>1:raise ValueError('ambiguous_previous_projection')
    if old and old[0]['host_source_call_evidence']['index_digest']!=bound._index_digest:raise ValueError('foreign_previous_projection')
    # Same calls are retained in the combined view; only this exact derived
    # entry is replaced. Source observations and all other history stay intact.
    if old and set(old[0])!={'host_source_call_evidence','boundary'}:raise ValueError('mixed_previous_projection')
    if old and old[0]!=collector.model_context(evidence):raise ValueError('modified_previous_projection')
    value['history']=[h for h in history if h not in old]+[dict(host_source_coverage=projection(bound,collector,evidence))]
    if type(max_chars) is not int or not 1<=max_chars<=24000 or len(json.dumps(value,ensure_ascii=False))>max_chars:raise SourceError('budget_exhausted','完整上下文超过原上限。')
    return value
