"""Finite native decoder evidence; no raw arguments, unknown keys or approval."""
FUNCTIONS=frozenset({'find_files','search_text','read_excerpt','submit_coverage_plan',
    'publish_section','report_gap','request_clarification'})
PHASES=frozenset({'off','read','planning','prose'})
REASONS=frozenset({'keys','title','compact_blocks','compact_gaps','compact_gap_chars',
    'compact_heading_chars','compact_text_chars','compact_evidence_count','model_validation',
    'arguments_json','arguments_object','duplicate_field'})
FIELDS=frozenset({'pattern','max_results','query','path_glob','path','start_line','end_line',
    'title','blocks','gaps','coverage_claims','message','question','options','criteria','limit',
    'max_files','max_matches'})
KEYS={'format','phase','function','reason','missing_fields','extra_fields_count','block_index','observed','limit'}

def safe_native_rejection(value):
    if type(value) is not dict or set(value)!=KEYS:return None
    if value['format']!='native_rejection_v1':return None
    if type(value['phase']) is not str or value['phase'] not in PHASES:return None
    if type(value['function']) is not str or value['function'] not in FUNCTIONS:return None
    if type(value['reason']) is not str or value['reason'] not in REASONS:return None
    missing=value['missing_fields']
    if type(missing) is not list or len(missing)>len(FIELDS):return None
    if any(type(x) is not str or x not in FIELDS for x in missing) or missing!=sorted(set(missing)):return None
    if any(type(value[k]) is not int or not 0<=value[k]<=14000 for k in ('extra_fields_count','block_index','observed','limit')):return None
    if value['block_index']>12:return None
    return dict(value,missing_fields=list(missing))

def projection(phase,function,reason,**counts):
    value=dict(format='native_rejection_v1',phase=phase,function=function,reason=reason,
        missing_fields=[],extra_fields_count=0,block_index=0,observed=0,limit=0)
    value.update(counts)
    result=safe_native_rejection(value)
    if result is None:raise ValueError('native_projection_shape')
    return result
