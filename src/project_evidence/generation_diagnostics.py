"""Finite post-return diagnostics. Never retain free text, errors or raw plans."""
from hashlib import sha256
import json,re

ACTIONS=frozenset({'tools','section_plan','final'})
STEPS=frozenset({'preload','model_dispatch','post_return','plan_snapshot','plan_accept','plan_record',
    'final_validation','claims_capture','claims_snapshot','event_persist','service_guard','hold_freeze',
    'freeze_complete','freeze_report','freeze_encode','freeze_post_guard','terminal_store'})
REASONS=frozenset({'plan_shape','plan_criteria','plan_duplicate_target','plan_unknown_target','plan_missing_gap',
    'plan_text','plan_required','unexpected_exception','source_error','event_not_saved',
    'body_claim_shape','body_claim_target','body_claim_quote','body_claim_field','plan_final_binding',
    'schema_error','type_error','key_error','value_error'})
TARGET=re.compile(r'^(?:[fc]:[0-9]{1,3}|g:(?:branches|queries|snapshot_fields|writes|write_inputs|payload_response|calls))$')


class GenerationRejected(ValueError):
    def __init__(self,reason,legacy=None):
        self.reason=reason if type(reason) is str and reason in REASONS else 'unexpected_exception'
        super().__init__(legacy or self.reason)


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return sha256(encoded(value).encode()).hexdigest()
def integer(value,cap):return type(value) is int and 0<=value<=cap


def plan_structure(draft,objective_count,catalog):
    """Identity of a redacted structural view, explicitly NOT the whole draft.

    Gap text and unknown target values are never copied or hashed. Their bounded
    lengths/counts plus the exact finite rejection code are sufficient to debug
    the contract families; this does not reconstruct an omitted provider reply.
    """
    keys=sorted(k for k in catalog if type(k) is str and len(k)<=32 and TARGET.fullmatch(k))
    known=set(keys);rows=[];truncated=False
    for row in draft['criteria'][:12]:
        targets=row['targets'];selected=[t for t in targets if t in known]
        rows.append(dict(number=row['number'],target_count=len(targets),known_targets=selected[:8],
            unknown_targets=sum(t not in known for t in targets),duplicate_targets=len(targets)-len(set(targets)),
            gap_chars=len(row['gap']),gap_present=bool(row['gap'].strip())))
        truncated=truncated or len(selected)>8
    value=dict(format='public_plan_structure_v1',identity_scope='safe_structure_only',objective_count=objective_count,
        catalog_digest=digest(keys),criteria_count=len(draft['criteria']),rows=rows,reference_sample_truncated=truncated)
    value['structure_digest']=digest(value)
    assert safe_plan_structure(value) is not None
    return value


def safe_plan_structure(value):
    if type(value) is not dict or set(value)!={'format','identity_scope','objective_count','catalog_digest','criteria_count','rows','reference_sample_truncated','structure_digest'}:return None
    if value['format']!='public_plan_structure_v1' or value['identity_scope']!='safe_structure_only':return None
    if not integer(value['objective_count'],12) or not integer(value['criteria_count'],12):return None
    if type(value['reference_sample_truncated']) is not bool:return None
    if any(type(value[k]) is not str or not re.fullmatch('[0-9a-f]{64}',value[k]) for k in ('catalog_digest','structure_digest')):return None
    rows=value['rows']
    if type(rows) is not list or len(rows)!=value['criteria_count']:return None
    clean=[]
    for row in rows:
        if type(row) is not dict or set(row)!={'number','target_count','known_targets','unknown_targets','duplicate_targets','gap_chars','gap_present'}:return None
        if not integer(row['number'],12) or not 1<=row['number']:return None
        if any(not integer(row[k],256) for k in ('target_count','unknown_targets','duplicate_targets')):return None
        if not integer(row['gap_chars'],600) or type(row['gap_present']) is not bool:return None
        targets=row['known_targets']
        if type(targets) is not list or len(targets)>8 or any(type(t) is not str or len(t)>32 or not TARGET.fullmatch(t) for t in targets):return None
        clean.append(dict(row,known_targets=list(targets)))
    result=dict(value,rows=clean)
    check={k:v for k,v in result.items() if k!='structure_digest'}
    if digest(check)!=result['structure_digest'] or len(encoded(result).encode())>4096:return None
    return result


def metadata(value):
    result={};kind=value.get('model_action_kind')
    if type(kind) is str and kind in ACTIONS:result['model_action_kind']=kind
    check=value.get('generation_check')
    if type(check) is dict and set(check)=={'step','reason'}:
        step,reason=check['step'],check['reason']
        if type(step) is str and step in STEPS and type(reason) is str and reason in REASONS:
            result['generation_check']=dict(step=step,reason=reason)
    plan=safe_plan_structure(value.get('public_plan_structure'))
    if plan is not None:result['public_plan_structure']=plan
    claims=safe_claim_structure(value.get('public_claim_structure'))
    if claims is not None:result['public_claim_structure']=claims
    return result


def freeze_check(step,error):
    """Types and minted finite codes only; never inspect exception text/args."""
    from pydantic import ValidationError
    step=step if type(step) is str and step in STEPS else 'hold_freeze'
    reason=(error.reason if type(error) is GenerationRejected else 'schema_error' if isinstance(error,ValidationError)
        else 'type_error' if isinstance(error,TypeError) else 'key_error' if isinstance(error,KeyError)
        else 'value_error' if isinstance(error,ValueError) else 'unexpected_exception')
    return dict(step=step,reason=reason)


CLAIM_FIELD=re.compile(r'^(?:title|block:[1-9][0-9]?:(?:heading|text)|gap:[1-9][0-9]?)$')
CLAIM_KINDS=frozenset({'explanation','unknown','relationship'})
CLAIM_COUNTS=('invalid_shapes','unknown_targets','unknown_fields','bad_quotes','unexpected_claims','duplicate_claims')


def claim_structure(claims,plan,output):
    """Bounded projection, not a response replay or release permission.

    Quote/gap/unknown field and target values never enter the projection or its
    digest. Counts use the same overlapping-match rule as final_report. Keep
    the first12 rows and replace the last with the first later failing row.
    """
    from .section_semantic_review import fields
    if type(claims) is not list or len(claims)>256:raise GenerationRejected('body_claim_shape')
    available=fields(output)
    expected={(r['number'],t) for r in plan['criteria'] for t in r['targets']}
    expected.update((r['number'],None) for r in plan['criteria'] if r['gap'].strip())
    seen=set();rows=[];counts=dict.fromkeys(CLAIM_COUNTS,0);late_problem=False
    for ordinal,raw in enumerate(claims,1):
        shape=type(raw) is dict and set(raw)=={'number','target','field','quote','kind'}
        raw=raw if type(raw) is dict else {}
        number=raw.get('number');number=number if type(number) is int and 1<=number<=12 else None
        target=raw.get('target');known=type(target) is str and target in plan['targets'] and TARGET.fullmatch(target)
        target_kind='known' if known else 'gap' if target is None else 'unknown'
        target=target if known else None
        field=raw.get('field');field=field if type(field) is str and field in available and CLAIM_FIELD.fullmatch(field) else None
        quote=raw.get('quote');chars=len(quote) if type(quote) is str and 1<=len(quote)<=1200 else None
        matches=0
        if field is not None and chars is not None:
            first=available[field].find(quote)
            if first>=0:matches=1+int(available[field].find(quote,first+1)>=0)
        key=(number,target);expected_claim=number is not None and target_kind!='unknown' and key in expected
        duplicate=expected_claim and key in seen
        if expected_claim:seen.add(key)
        kind=raw.get('kind');kind=kind if type(kind) is str and kind in CLAIM_KINDS else None
        bad=dict(invalid_shapes=not shape or number is None or chars is None or kind is None,
            unknown_targets=target_kind=='unknown',unknown_fields=field is None,bad_quotes=matches!=1,
            unexpected_claims=not expected_claim,duplicate_claims=duplicate)
        for name,found in bad.items():counts[name]+=int(found)
        row=dict(ordinal=ordinal,number=number,target_kind=target_kind,target=target,field=field,kind=kind,
            quote_chars=chars,quote_matches=matches,expected=expected_claim,duplicate=duplicate)
        if len(rows)<12:rows.append(row)
        elif any(bad.values()) and not late_problem:rows[-1]=row;late_problem=True
    value=dict(format='public_body_claim_structure_v1',identity_scope='safe_structure_only',
        objective_count=len(plan['public_objectives']),plan_digest=plan['plan_digest'],output_digest=digest(output),
        claim_count=len(claims),rows=rows,reference_sample_truncated=len(claims)>12,counts=counts)
    value['structure_digest']=digest(value)
    if safe_claim_structure(value) is None:raise GenerationRejected('body_claim_shape')
    return value


def safe_claim_structure(value):
    keys={'format','identity_scope','objective_count','plan_digest','output_digest','claim_count','rows','reference_sample_truncated','counts','structure_digest'}
    if type(value) is not dict or set(value)!=keys:return None
    if value['format']!='public_body_claim_structure_v1' or value['identity_scope']!='safe_structure_only':return None
    if not integer(value['objective_count'],12) or value['objective_count']<1 or not integer(value['claim_count'],256):return None
    if any(type(value[k]) is not str or not re.fullmatch('[0-9a-f]{64}',value[k]) for k in ('plan_digest','output_digest','structure_digest')):return None
    rows=value['rows'];counts=value['counts']
    if type(rows) is not list or len(rows)!=min(12,value['claim_count']):return None
    if type(value['reference_sample_truncated']) is not bool or value['reference_sample_truncated']!=(value['claim_count']>12):return None
    if type(counts) is not dict or set(counts)!=set(CLAIM_COUNTS) or any(not integer(n,value['claim_count']) for n in counts.values()):return None
    ordinals=[];clean=[]
    for row in rows:
        if type(row) is not dict or set(row)!={'ordinal','number','target_kind','target','field','kind','quote_chars','quote_matches','expected','duplicate'}:return None
        if not integer(row['ordinal'],value['claim_count']) or row['ordinal']<1:return None
        if row['number'] is not None and (not integer(row['number'],12) or row['number']<1):return None
        if row['target_kind'] not in ('known','gap','unknown'):return None
        if row['target_kind']=='known':
            if type(row['target']) is not str or not TARGET.fullmatch(row['target']):return None
        elif row['target'] is not None:return None
        if row['field'] is not None and (type(row['field']) is not str or not CLAIM_FIELD.fullmatch(row['field'])):return None
        if row['kind'] is not None and (type(row['kind']) is not str or row['kind'] not in CLAIM_KINDS):return None
        if row['quote_chars'] is not None and (not integer(row['quote_chars'],1200) or row['quote_chars']<1):return None
        if not integer(row['quote_matches'],2) or any(type(row[k]) is not bool for k in ('expected','duplicate')):return None
        ordinals.append(row['ordinal']);clean.append(dict(row))
    if ordinals!=sorted(set(ordinals)):return None
    result=dict(value,rows=clean,counts=dict(counts))
    if digest({k:v for k,v in result.items() if k!='structure_digest'})!=result['structure_digest'] or len(encoded(result).encode())>4096:return None
    return result
