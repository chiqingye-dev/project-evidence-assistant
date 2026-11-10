"""Untrusted source-call assertion sidecar -> bounded evidence review result.

Even supported call syntax never certifies arbitrary prose or the six section
criteria. Output is always held for independent semantic review; no publishing,
repair, I/O, retries or model requests exist in this module.
"""
from hashlib import sha256
import json
import re
from typing import Literal
from pydantic import Field,model_validator,ValidationError
from .content_contract import StrictContract
from .learning_contract import SectionExplanation
from .call_binding import BoundIndex,_checked,Rejected as BindingRejected
from . import call_evidence as syntax


class Rejected(ValueError):
    def __init__(self,reason):
        super().__init__(reason if reason in {'claim_shape','index_binding','output_binding','location','span','overlap','call_binding','evidence_not_cited'} else 'claim_shape')


class Claim(StrictContract):
    index_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    output_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    call_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    field:Literal['title','block_text','block_heading','gap']
    block:int|None=Field(default=None,ge=1,le=12)
    gap:int|None=Field(default=None,ge=1,le=12)
    start:int=Field(ge=0,le=14000)
    end:int=Field(ge=1,le=14000)
    kind:Literal['invoke','pass_callable','register_callable','execute_callable']

    @model_validator(mode='after')
    def shape(self):
        if self.start>=self.end:raise ValueError('invalid_span')
        if self.field.startswith('block_'):
            if self.block is None or self.gap is not None:raise ValueError('invalid_location')
        elif self.field=='gap':
            if self.gap is None or self.block is not None:raise ValueError('invalid_location')
        elif self.block is not None or self.gap is not None:raise ValueError('invalid_location')
        return self


class Proposal(StrictContract):
    """Model/reviewer data: host computes hashes/offsets after output exists."""
    call_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    field:Literal['title','block_text','block_heading','gap']
    block:int|None=Field(default=None,ge=1,le=12)
    gap:int|None=Field(default=None,ge=1,le=12)
    quote:str=Field(min_length=1,max_length=1200)
    kind:Literal['invoke','pass_callable','register_callable','execute_callable']


def _output(output,index):
    try:
        data=_checked(index)
        value=SectionExplanation.model_validate(output).model_dump()
    except (ValueError,TypeError,BindingRejected):raise Rejected('output_binding') from None
    if value['topic']!=data['topic'] or value['section_id']!=data['section_id']:raise Rejected('output_binding')
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return value,data,sha256(encoded.encode()).hexdigest()


def output_digest(output,index):return _output(output,index)[2]


def _fields(value):
    fields={('title',None,None):(value['title'],[])}
    for i,block in enumerate(value['blocks'],1):
        fields[('block_text',i,None)]=(block['text'],block['evidence_ids'])
        fields[('block_heading',i,None)]=(block['heading'],block['evidence_ids'])
    for i,gap in enumerate(value['gaps'],1):fields[('gap',None,i)]=(gap,[])
    return fields


def bind_proposals(output,index,proposals):
    """Host-bind exact unique quotes; this seals identity, NOT truth or approval."""
    value,_,digest=_output(output,index)
    if type(proposals) is not list or len(proposals)>64:raise Rejected('claim_shape')
    fields=_fields(value);claims=[]
    for raw in proposals:
        try:proposal=Proposal.model_validate(raw)
        except (ValidationError,TypeError):raise Rejected('claim_shape') from None
        key=(proposal.field,proposal.block,proposal.gap)
        if key not in fields:raise Rejected('location')
        text=fields[key][0];start=text.find(proposal.quote)
        if start<0 or text.find(proposal.quote,start+1)>=0:raise Rejected('span')
        data=proposal.model_dump();data.pop('quote')
        try:claim=Claim(**data,index_digest=index.digest,output_digest=digest,start=start,end=start+len(proposal.quote))
        except ValidationError:raise Rejected('claim_shape') from None
        claims.append(claim.model_dump())
    return claims


def review(output,index,claims):
    if type(index) is not BoundIndex:raise Rejected('index_binding')
    value,data,digest=_output(output,index)
    if type(claims) is not list or len(claims)>64:raise Rejected('claim_shape')
    fields=_fields(value);occupied={};findings=[];supported=[]
    by_call={c['call_id']:c for s in data['sources'].values() for c in s['calls']}
    for position,raw in enumerate(claims,1):
        try:claim=Claim.model_validate(raw)
        except (ValidationError,TypeError):raise Rejected('claim_shape') from None
        if claim.index_digest!=index.digest:raise Rejected('index_binding')
        if claim.output_digest!=digest:raise Rejected('output_binding')
        key=(claim.field,claim.block,claim.gap)
        if key not in fields:raise Rejected('location')
        text,ids=fields[key]
        if claim.end>len(text):raise Rejected('span')
        ranges=occupied.setdefault(key,[])
        if any(claim.start<b and a<claim.end for a,b in ranges):raise Rejected('overlap')
        ranges.append((claim.start,claim.end))
        call=by_call.get(claim.call_id)
        if call is None:raise Rejected('call_binding')
        if call['evidence_id'] not in ids:raise Rejected('evidence_not_cited')
        span=text[claim.start:claim.end]
        names=re.findall(syntax.IDENT+r'(?:\.'+syntax.IDENT+r')*',span)
        item=dict(claim=position,field=claim.field,block=claim.block,gap=claim.gap,call_id=claim.call_id)
        if not any(name==call['callee'] or '.' not in name and name==call['name'] for name in names):
            findings.append(dict(item,reason='claim_target_not_in_span'));continue
        if claim.kind=='invoke':
            if syntax.TERMS.search(span):findings.append(dict(item,reason='claim_kind_text_mismatch'))
            else:supported.append(dict(item,syntax_support='invocation_exists',prose_semantics='not_certified'))
        elif claim.kind=='pass_callable':
            if call['passing']!='explicit_callable':findings.append(dict(item,reason='passing_callable_unproven'))
            elif re.search(r'登记|注册|执行|触发|register|execute|immediate',span,re.I):
                findings.append(dict(item,reason='claim_exceeds_argument_syntax'))
            else:supported.append(dict(item,syntax_support='explicit_callable_argument',prose_semantics='not_certified'))
        else:findings.append(dict(item,reason='receiver_semantics_unverified'))
    # Coverage is fail-closed for recognized callback/registration language.
    # A sidecar labelled "invoke" does not certify the prose: both this scan
    # and the independently source-bound audit still run on the actual body.
    for key,(text,_) in fields.items():
        for unit in re.finditer(r'[^\n。；;，,]+',text):
            first=syntax.TERMS.search(unit[0])
            if first is None or syntax._uncertainty_only(unit[0],first.start()):continue
            for term in syntax.TERMS.finditer(unit[0]):
                a,b=unit.start()+term.start(),unit.start()+term.end()
                if not any(x<=a<=b<=y for x,y in occupied.get(key,[])):
                    findings.append(dict(field=key[0],block=key[1],gap=key[2],reason='uncovered_call_language'))
    body_audit=syntax._audit_output_sources(value,data['sources'])
    return dict(status='needs_review' if findings or body_audit['findings'] else 'syntax_supported_only',
        index_digest=index.digest,output_digest=digest,claim_findings=findings,supported_call_syntax=supported,
        bounded_body_audit=body_audit,semantic_review_required=True,semantic_verified=False,
        publication_approval=False,publication_performed=False,output_modified=False)
