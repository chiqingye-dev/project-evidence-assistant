"""Host-configured independent source-assisted review, not semantic proof.

The reviewer is a trusted host function, never a model tool/HTTP permission.
Its data is still checked for exact criterion, output and source coverage. The
host judgement of entailment remains explicit; matching quotes is not proof.
No model dispatch, source read, answer repair or automatic positive reviewer.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Literal

from pydantic import Field,ValidationError
from .content_contract import StrictContract,_safe_text
from .section_review import FrozenCandidate,encoded
from .call_claim_contract import Proposal,bind_proposals,review as review_calls
from .source_policy import normalize_github_repository


def digest(value):return sha256(encoded(value).encode()).hexdigest()


_MINT=object()


class CheckedReview:
    __slots__=('_text',)
    def __init__(self,value,*,mint=None):
        if mint is not _MINT:raise ValueError('review_binding')
        object.__setattr__(self,'_text',encoded(value))
    def __setattr__(self,*_):raise ValueError('review_immutable')
    def view(self):return json.loads(self._text)


@dataclass(frozen=True)
class ReviewContract:
    document_sha256:str
    case_id:str
    public_url:str
    commit:str
    required:tuple[str,...]

    def __post_init__(self):
        if (not isinstance(self.document_sha256,str) or not re.fullmatch('[0-9a-f]{64}',self.document_sha256)
                or not isinstance(self.commit,str) or not re.fullmatch('[0-9a-f]{40}',self.commit)
                or not isinstance(self.case_id,str) or re.fullmatch('[A-Za-z0-9_-]{1,80}',self.case_id) is None
                or type(self.required) is not tuple or not 1<=len(self.required)<=12
                or any(not isinstance(x,str) or not 1<=len(x)<=2000 for x in self.required)
                or len(set(self.required))!=len(self.required)):
            raise ValueError('review_contract')
        normalize_github_repository(self.public_url)

    @classmethod
    def from_document(cls,raw,expected_sha256,case_id):
        if (type(raw) is not bytes or not 1<=len(raw)<=128000 or
                not re.fullmatch('[0-9a-f]{64}',expected_sha256) or sha256(raw).hexdigest()!=expected_sha256):
            raise ValueError('review_contract')
        value=json.loads(raw)
        tasks=[t for t in value['tasks'] if t['id']==case_id]
        if len(tasks)!=1 or tasks[0]['goal']!='section_explanation':raise ValueError('review_contract')
        required=tasks[0]['required']
        if (type(required) is not list or not 1<=len(required)<=12 or len(set(required))!=len(required)
                or any(not isinstance(x,str) or not 1<=len(x)<=2000 for x in required)
                or not re.fullmatch('[0-9a-f]{40}',value['commit'])):raise ValueError('review_contract')
        return cls(expected_sha256,case_id,normalize_github_repository(value['public_url']),value['commit'],tuple(required))

    @property
    def digest(self):return digest(dict(document_sha256=self.document_sha256,case_id=self.case_id,
        public_url=self.public_url,commit=self.commit,required=self.required))


@dataclass(frozen=True)
class HostReviewer:
    identity:str
    mode:Literal['scripted','host_source_assisted']
    inspect:object

    def __post_init__(self):
        if (not isinstance(self.identity,str) or re.fullmatch('[A-Za-z0-9_.:/-]{1,80}',self.identity) is None
                or self.mode not in ('scripted','host_source_assisted') or not callable(self.inspect)):
            raise ValueError('host_reviewer')
        _safe_text(self.identity)


class BodyQuote(StrictContract):
    field:str=Field(min_length=1,max_length=40)
    quote:str=Field(min_length=1,max_length=4000)


class SourceQuote(StrictContract):
    evidence_id:str=Field(min_length=1,max_length=80)
    start_line:int=Field(ge=1)
    end_line:int=Field(ge=1)
    quote:str=Field(min_length=1,max_length=12000)


class Criterion(StrictContract):
    number:int=Field(ge=1,le=12)
    verdict:Literal['pass','fail','unverified']
    reason:str=Field(min_length=1,max_length=1200)
    body_quotes:list[BodyQuote]=Field(max_length=12)
    source_quotes:list[SourceQuote]=Field(max_length=12)


class CoveredField(StrictContract):
    field:str=Field(min_length=1,max_length=40)
    sha256:str=Field(pattern='^[0-9a-f]{64}$')


class Finding(BodyQuote):
    reason:str=Field(min_length=1,max_length=1200)


class Assessment(StrictContract):
    task_id:str=Field(min_length=1,max_length=80)
    output_digest:str=Field(pattern='^[0-9a-f]{64}$')
    index_digest:str=Field(pattern='^[0-9a-f]{64}$')
    contract_digest:str=Field(pattern='^[0-9a-f]{64}$')
    covered_fields:list[CoveredField]=Field(min_length=1,max_length=49)
    criteria:list[Criterion]=Field(min_length=1,max_length=12)
    call_proposals:list[Proposal]=Field(max_length=64)
    critical_findings:list[Finding]=Field(max_length=24)


def fields(output):
    result={'title':output['title']}
    for i,block in enumerate(output['blocks'],1):
        result[f'block:{i}:heading']=block['heading'];result[f'block:{i}:text']=block['text']
    for i,gap in enumerate(output['gaps'],1):result[f'gap:{i}']=gap
    return result


def packet(candidate,collector,contract):
    if type(candidate) is not FrozenCandidate or type(contract) is not ReviewContract:raise ValueError('review_binding')
    value=candidate.view()
    if (contract.commit!=value['commit'] or normalize_github_repository(contract.public_url)!=normalize_github_repository(value['public_url'])):
        raise ValueError('review_binding')
    refs=value['index']['accepted_reads']
    plain=[{k:v for k,v in ref.items() if k!='excerpt_sha256'} for ref in refs]
    index=collector.index(plain)
    if index.digest!=value['index_digest'] or digest(value['output'])!=value['output_digest']:
        raise ValueError('review_binding')
    source={eid:dict(ref,**{'text':chunk['text']}) for eid,(ref,chunk) in collector._accepted.items()}
    result=dict(format='independent_section_review_packet_v1',task_id=value['task_id'],
        project_id=value['project_id'],output=value['output'],output_digest=value['output_digest'],
        index_digest=value['index_digest'],contract_digest=contract.digest,case_id=contract.case_id,
        contract_document_sha256=contract.document_sha256,required=list(contract.required),
        fields=[dict(field=k,sha256=sha256(v.encode()).hexdigest(),text=v) for k,v in fields(value['output']).items()],
        source=source,call_index=index.view(),call_audit=value['audit'],
        notice='Review the whole original section, including title/headings/diagrams/gaps, against every original criterion. Quotes bind identity, not entailment. Never infer receiver behavior or runtime proof. This is host source-assisted judgement, not personal acceptance.')
    if len(encoded(result).encode())>128000:raise ValueError('review_packet_limit')
    return result,index


def _body_quote(item,available):
    text=available.get(item.field)
    start=text.find(item.quote) if text is not None else -1
    if start<0 or text.find(item.quote,start+1)>=0:raise ValueError('review_body_quote')


def assess(candidate,collector,contract,reviewer,execution_mode):
    if type(reviewer) is not HostReviewer:raise ValueError('host_reviewer')
    if execution_mode=='real' and reviewer.mode!='host_source_assisted':raise ValueError('scripted_real_review')
    view,index=packet(candidate,collector,contract)
    # The callback sees a detached data copy; it cannot replace live authority.
    raw=reviewer.inspect(deepcopy(view))
    try:checked=Assessment.model_validate(raw)
    except (ValidationError,ValueError,TypeError):raise ValueError('review_shape') from None
    for key in ('task_id','output_digest','index_digest','contract_digest'):
        if getattr(checked,key)!=view[key]:raise ValueError('review_binding')
    expected={f['field']:f['sha256'] for f in view['fields']}
    covered={f.field:f.sha256 for f in checked.covered_fields}
    if len(covered)!=len(checked.covered_fields) or covered!=expected:raise ValueError('review_coverage')
    if sorted(c.number for c in checked.criteria)!=list(range(1,len(contract.required)+1)):
        raise ValueError('review_criteria')
    available=fields(view['output'])
    for item in checked.critical_findings:
        _body_quote(item,available);_safe_text(item.reason)
    for criterion in checked.criteria:
        _safe_text(criterion.reason)
        if criterion.verdict=='pass' and (not criterion.body_quotes or not criterion.source_quotes):
            raise ValueError('review_support_missing')
        for quote in criterion.body_quotes:_body_quote(quote,available)
        cited=set()
        for quote in criterion.body_quotes:
            match=re.fullmatch(r'block:(\d+):(heading|text)',quote.field)
            if match:cited.update(view['output']['blocks'][int(match[1])-1]['evidence_ids'])
        for quote in criterion.source_quotes:
            source=view['source'].get(quote.evidence_id)
            if (source is None or not source['start_line']<=quote.start_line<=quote.end_line<=source['end_line']):
                raise ValueError('review_source_quote')
            if criterion.verdict=='pass' and quote.evidence_id not in cited:raise ValueError('review_source_not_cited')
            text='\n'.join(source['text'].split('\n')[quote.start_line-source['start_line']:quote.end_line-source['start_line']+1])
            if quote.quote!=text:raise ValueError('review_source_quote')
    claims=bind_proposals(view['output'],index,[p.model_dump() for p in checked.call_proposals])
    call_review=review_calls(view['output'],index,claims)
    passed=sum(c.verdict=='pass' for c in checked.criteria)
    approved=(passed==len(contract.required) and not checked.critical_findings and
        not call_review['claim_findings'] and not call_review['bounded_body_audit']['findings'])
    result=dict(format='independent_section_review_v1',task_id=view['task_id'],project_id=view['project_id'],
        commit=contract.commit,output_digest=view['output_digest'],index_digest=view['index_digest'],
        contract_digest=contract.digest,contract_document_sha256=contract.document_sha256,case_id=contract.case_id,
        reviewer=reviewer.identity,review_mode=reviewer.mode if execution_mode=='real' else 'scripted',
        required=list(contract.required),assessment=checked.model_dump(),call_review=call_review,
        passed=passed,total=len(contract.required),source_assisted_review_passed=approved,
        publication_approval=approved,publication_performed=False,semantic_verified=False,
        personal_acceptance=False,output_modified=False)
    if len(encoded(result).encode())>64000:raise ValueError('review_receipt_limit')
    return CheckedReview(result,mint=_MINT)


class ReviewStore:
    """Bounded append-only receipts and one atomic hold-to-publication CAS."""
    def __init__(self,projects,*,create=False,contract=None):
        self.projects=projects;self.contract=contract
        if create:
            with projects._connection() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS section_review_receipts (
                    task_id TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 8),
                    payload_json TEXT NOT NULL CHECK(length(CAST(payload_json AS BLOB))<=64000),
                    PRIMARY KEY(task_id,sequence))''')

    def _append(self,db,task_id,receipt):
        seq=db.execute('SELECT count(*) FROM section_review_receipts WHERE task_id=?',(task_id,)).fetchone()[0]+1
        if seq>8:raise ValueError('review_capacity')
        db.execute('INSERT INTO section_review_receipts VALUES (?,?,?)',(task_id,seq,encoded(receipt)))

    @staticmethod
    def _held_transition(db,task_store,task):
        # Keep TaskStore._write's original active-only CAS unchanged. Only this
        # transaction may transition a held candidate, never another terminal.
        cursor=db.execute("UPDATE agent_tasks SET status=?,payload_json=? WHERE id=? AND project_id=? "
            "AND commit_sha=? AND status='needs_review'",(task['status'],task_store._encode(task),
                task['id'],task['project_id'],task['commit']))
        if cursor.rowcount!=1:raise ValueError('review_binding')

    def latest(self,project_id,task_id):
        with self.projects._connection() as db:
            if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='section_review_receipts'").fetchone():return None
            row=db.execute('SELECT r.payload_json FROM section_review_receipts r JOIN agent_tasks t ON t.id=r.task_id '
                'WHERE t.project_id=? AND r.task_id=? ORDER BY r.sequence DESC LIMIT 1',(str(project_id),str(task_id))).fetchone()
        return json.loads(row[0]) if row else None

    def summary(self,project_id,task_id):
        row=self.latest(project_id,task_id)
        if row is None:return None
        return {k:row[k] for k in ('status','source_assisted_review_passed','publication_approval','publication_performed',
            'semantic_verified','output_digest','index_digest','contract_digest','review_mode','passed','total') if k in row}

    def require_capacity(self,project_id,task_id):
        with self.projects._connection() as db:
            count=db.execute('SELECT count(*) FROM section_review_receipts r JOIN agent_tasks t ON t.id=r.task_id '
                'WHERE t.project_id=? AND r.task_id=?',(str(project_id),str(task_id))).fetchone()[0]
        if count>=8:raise ValueError('review_capacity')

    def finish(self,task_store,learning,candidate,receipt):
        if type(candidate) is not FrozenCandidate or type(receipt) is not CheckedReview:raise ValueError('review_binding')
        receipt=receipt.view()
        if self.contract is None or receipt['contract_digest']!=self.contract.digest:raise ValueError('review_binding')
        value=candidate.view();task_id=value['task_id']
        with self.projects._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT t.payload_json,p.commit_sha,h.payload_json FROM agent_tasks t '
                'JOIN projects p ON p.id=t.project_id JOIN held_section_candidates h ON h.task_id=t.id '
                'WHERE t.project_id=? AND t.id=?',(value['project_id'],task_id)).fetchone()
            if row is None:raise ValueError('review_binding')
            task=json.loads(row[0])
            references=[{k:v for k,v in ref.items() if k!='excerpt_sha256'} for ref in value['index']['accepted_reads']]
            if (task['status']!='needs_review' or row[1]!=value['commit'] or json.loads(row[2])!=value
                    or encoded(task['result'])!=encoded(value['output'])
                    or task.get('quality_verified') is not False or encoded(task['evidence'])!=encoded(references)
                    or any(receipt[k]!=value[k] for k in ('task_id','project_id','commit','output_digest','index_digest'))):
                raise ValueError('review_binding')
            approved=receipt['publication_approval'] is True
            task['status']='completed' if approved else 'review_rejected'
            task['message']='独立源码辅助复核通过，原样正文已保存；不代表运行验证或个人掌握。' if approved else '独立源码辅助复核未通过；原候选及复核原因保留，未发布。'
            from .task_storage import now
            task['updated_at']=now()
            self._held_transition(db,task_store,task)
            if approved:learning.save_from_task(value['project_id'],task_id,transaction=db)
            final=dict(receipt,status='released' if approved else 'review_rejected',publication_performed=approved)
            self._append(db,task_id,final)
        return final

    def error(self,project_id,task_id,candidate,reason):
        value=candidate.view()
        if reason not in ('invalid_review','review_timeout','review_state_changed','publication_failed'):reason='invalid_review'
        receipt=dict(status='review_error',reason=reason,task_id=task_id,project_id=str(project_id),
            output_digest=value['output_digest'],index_digest=value['index_digest'],
            source_assisted_review_passed=False,publication_approval=False,publication_performed=False,semantic_verified=False)
        with self.projects._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM agent_tasks WHERE project_id=? AND id=?',(str(project_id),task_id)).fetchone():return
            self._append(db,task_id,receipt)

    def cancel(self,task_store,project_id,task_id):
        with self.projects._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload_json FROM agent_tasks WHERE project_id=? AND id=?',(str(project_id),str(task_id))).fetchone()
            if row is None:return False
            task=json.loads(row[0])
            if task['status']!='needs_review':return False
            from .task_storage import now
            task.update(status='cancelled',message='已取消待审候选；原正文与证据保留，不会放行。',updated_at=now())
            self._held_transition(db,task_store,task)
        return True
