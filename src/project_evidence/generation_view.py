"""Lossless syntax-view transport for opt-in planning; never source authority."""
from copy import deepcopy

FORMAT='coverage_and_calls_compact_v1'


def _span(start,end):return start if start==end else [start,end]


def _range(value):
    if type(value) is int and value>=1:return value,value
    if (type(value) is list and len(value)==2 and all(type(x) is int for x in value)
            and 1<=value[0]<=value[1]):return value
    raise ValueError('coverage_span')


def _code(values,index):
    if type(index) is not int or not 0<=index<len(values):raise ValueError('coverage_code')
    return values[index]


def compact(view):
    if view.get('format')!='coverage_and_calls_rows_v1':raise ValueError('coverage_format')
    result=deepcopy(view);result['format']=FORMAT
    result['fact_columns']=['kind','evidence','span','subject_or_null_kind_name','source_value']
    result['facts']=[[k,e,_span(a,b),None if s==view['kind_codes'][k] else s,v] for k,e,a,b,s,v in view['facts']]
    result['passing_codes']=list(dict.fromkeys(r[4] for r in view['calls']))
    result['call_columns']=['evidence','span','callee','optional_passing_code_default_0']
    prefixes=[r[3].rsplit('.',1)[0] for r in view['calls'] if '.' in r[3]]
    result['receiver_codes']=list(dict.fromkeys(p for p in prefixes if len(p)>=8 and prefixes.count(p)>=3))
    def callee(name):
        if '.' in name:
            receiver,method=name.rsplit('.',1)
            if receiver in result['receiver_codes']:return [result['receiver_codes'].index(receiver),method]
        return name
    result['calls']=[[e,_span(a,b),callee(name),result['passing_codes'].index(p)] for e,a,b,name,p in view['calls']]
    result['calls']=[row[:-1] if row[-1]==0 else row for row in result['calls']]
    result['call_columns'][2]='callee_or_receiver_code_and_name'
    result['status_codes']=list(dict.fromkeys(r[1] for r in view['call_source_status']))
    result['call_source_status']=[[e,result['status_codes'].index(s)] for e,s in view['call_source_status']]
    result['unsupported_reason_codes']=list(dict.fromkeys(r['reason'] for r in view['unsupported']))
    result['unsupported']=[[view['evidence_ids'].index(r['evidence_id']),result['unsupported_reason_codes'].index(r['reason'])] for r in view['unsupported']]
    result['unsupported_columns']=['evidence','reason_code']
    return result


def expand(value):
    """Data-only inverse for QA and consumers; no AcceptedReads/BoundIndex mint."""
    if value.get('format')!=FORMAT:raise ValueError('coverage_format')
    result=deepcopy(value);result['format']='coverage_and_calls_rows_v1'
    result['fact_columns']=['kind','evidence','start','end','subject','source_value']
    result['facts']=[[k,e,*_range(span),_code(value['kind_codes'],k) if s is None else s,v] for k,e,span,s,v in value['facts']]
    result['call_columns']=['evidence','start','end','callee','passing']
    def callee(name):
        if type(name) is str:return name
        if type(name) is list and len(name)==2 and type(name[1]) is str:
            return _code(value['receiver_codes'],name[0])+'.'+name[1]
        raise ValueError('coverage_callee')
    result['calls']=[]
    for row in value['calls']:
        if len(row) not in (3,4):raise ValueError('coverage_call_shape')
        e,span,name=row[:3];p=row[3] if len(row)==4 else 0
        result['calls'].append([e,*_range(span),callee(name),_code(value['passing_codes'],p)])
    result['call_source_status']=[[e,_code(value['status_codes'],s)] for e,s in value['call_source_status']]
    result['unsupported']=[dict(evidence_id=_code(value['evidence_ids'],e),reason=_code(value['unsupported_reason_codes'],r)) for e,r in value['unsupported']]
    for key in ('passing_codes','receiver_codes','status_codes','unsupported_reason_codes','unsupported_columns'):result.pop(key)
    return result
