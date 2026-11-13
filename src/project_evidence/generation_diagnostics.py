"""Finite post-return diagnostics. Never retain free text, errors or raw plans."""
from hashlib import sha256
import json,re

ACTIONS=frozenset({'tools','section_plan','final'})
STEPS=frozenset({'preload','model_dispatch','post_return','plan_snapshot','plan_accept','plan_record',
    'final_validation','claims_capture','event_persist','service_guard','hold_freeze','terminal_store'})
REASONS=frozenset({'plan_shape','plan_criteria','plan_duplicate_target','plan_unknown_target','plan_missing_gap',
    'plan_text','plan_required','unexpected_exception','source_error','event_not_saved'})
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
    return result
