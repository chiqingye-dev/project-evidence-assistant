"""Offline syntax inventory and conservative coverage diagnostics, never approval.
No source I/O, supplier API, answer repair, runtime assertion or live authority.
"""
from hashlib import sha256
import json,re
from pathlib import PurePosixPath

def encode(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(v):return sha256(encode(v).encode()).hexdigest()

def reject_constant(_):raise ValueError('nonfinite_json_literal')

def mask(text):
    if len(text)>24000 or re.search(r'\\u+[0-9a-fA-F]{4}',text):raise ValueError('unsupported_java')
    out=list(text);literals=[];i=0
    while i<len(text):
        a=i
        if text.startswith('//',i):
            b=text.find('\n',i);b=len(text) if b<0 else b
        elif text.startswith('/*',i):
            b=text.find('*/',i+2)
            if b<0:raise ValueError('incomplete_comment')
            b+=2
        elif text[i] in '\"\'':
            delimiter='"""' if text.startswith('"""',i) else text[i];b=i+len(delimiter)
            while b<len(text):
                if text[b]=='\\':b+=2;continue
                if text.startswith(delimiter,b):break
                b+=1
            if b>=len(text):raise ValueError('incomplete_literal')
            b+=len(delimiter);literals.append((a,b,delimiter))
        else:i+=1;continue
        for j in range(a,b):
            if text[j]!='\n':out[j]=' '
        i=b
    return ''.join(out),literals

def split_items(text):
    parts=[];start=0;depth=0;quote=None;i=0
    while i<len(text):
        c=text[i]
        if quote:
            if c==quote:
                if i+1<len(text) and text[i+1]==quote:i+=2;continue
                quote=None
        elif c in "'\"":quote=c
        elif c=='(':depth+=1
        elif c==')':
            depth-=1
            if depth<0:raise ValueError('unbalanced_sql')
        elif c==',' and depth==0:parts.append(text[start:i].strip());start=i+1
        i+=1
    if quote or depth:raise ValueError('unbalanced_sql')
    parts.append(text[start:].strip())
    if any(not p for p in parts):raise ValueError('empty_sql_item')
    return parts

def atoms(text):
    return list(dict.fromkeys(re.findall(r'[A-Za-z_$][A-Za-z0-9_$]*|\d+|>=|<=|!=|==',text)))[:32]

def inventory(reads):
    if not isinstance(reads,list) or not 1<=len(reads)<=16:raise ValueError('reads')
    facts=[];gaps=[];ids=set()
    for read in reads:
        eid=read['evidence_id'];text=read['text'];start=read['start_line']
        path=read['path']
        if not isinstance(path,str):raise ValueError('source_path')
        parts=PurePosixPath(path).parts
        if not isinstance(path,str) or not path or '\\' in path or PurePosixPath(path).is_absolute() or any(x in ('.','..','.git') for x in parts) or ':' in path or not path.endswith('.java') or str(PurePosixPath(path))!=path:raise ValueError('source_path')
        if not isinstance(eid,str) or re.fullmatch(r'[A-Za-z0-9_-]{1,80}',eid) is None or not isinstance(text,str):raise ValueError('source_shape')
        if eid in ids or read.get('truncated') is not False:raise ValueError('duplicate_or_truncated')
        ids.add(eid)
        if type(read['end_line']) is not int or type(start) is not int or start<1 or len(text.split('\n'))!=read['end_line']-start+1:raise ValueError('line_binding')
        if sha256(text.encode()).hexdigest()!=read['excerpt_sha256']:raise ValueError('text_binding')
        def add(kind,a,b,subject,anchors):
            raw=text[a:b];item=dict(kind=kind,evidence_id=eid,path=read['path'],start_line=start+text[:a].count('\n'),end_line=start+text[:b-1].count('\n'),source_quote=raw,subject=subject,anchors=anchors)
            item['id']=digest(item)[:24];facts.append(item)
            if len(facts)>256:raise ValueError('fact_limit')
        try:code,literals=mask(text)
        except ValueError as e:gaps.append(dict(evidence_id=eid,reason=str(e)));continue
        for pattern,kind in [(r'@Transactional\b','transaction_declaration'),(r'\b(?:final\s+)?int\s+(\w+)\s*=\s*\d+\s*;','constant'),(r'\bif\s*\(','condition'),(r'\bthrow\s+new\s+\w+\s*\(','exception'),(r'\breturn\s+new\s+\w+\s*\(','return_value')]:
            for m in re.finditer(pattern,code):
                a,b=m.span()
                if kind in ('condition','exception','return_value'):
                    depth=1;i=b
                    while i<len(code) and depth:
                        depth+=(code[i]=='(')-(code[i]==')');i+=1
                    if depth:gaps.append(dict(evidence_id=eid,reason='incomplete_'+kind));continue
                    b=i
                add(kind,a,b,kind,atoms(text[a:b]))
        for a,b,delimiter in literals:
            if delimiter!='"""':continue
            raw=text[a+3:b-3]
            if raw.strip().startswith('{'):
                try:value=json.loads(raw,parse_constant=reject_constant)
                except (ValueError,RecursionError):gaps.append(dict(evidence_id=eid,reason='unsupported_json_literal'));continue
                if not isinstance(value,dict):continue
                for key in value:add('payload_field',a,b,key,[key]+([] if value[key]=='%s' else atoms(str(value[key]))))
        for annotation in re.finditer(r'@(Select|Insert)\b',code):
            a=annotation.start();literal=next((x for x in literals if x[0]>=annotation.end()),None)
            if literal is None or literal[2]!='"""' or not re.fullmatch(r'\s*\(\s*',text[annotation.end():literal[0]]):
                gaps.append(dict(evidence_id=eid,reason='unsupported_annotation'));continue
            la,lb,_=literal;sql=text[la+3:lb-3];base=la+3
            if re.search(r'--|/\*',sql):gaps.append(dict(evidence_id=eid,reason='sql_comments_unsupported'));continue
            if annotation[1]=='Insert':
                m=re.fullmatch(r'\s*INSERT\s+INTO\s+(\w+)\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*',sql,re.I|re.S)
                if not m:gaps.append(dict(evidence_id=eid,reason='unsupported_insert'));continue
                try:columns,values=split_items(m[2]),split_items(m[3])
                except ValueError:gaps.append(dict(evidence_id=eid,reason='unbalanced_insert'));continue
                if len(columns)!=len(values):gaps.append(dict(evidence_id=eid,reason='insert_arity'));continue
                for col,val in zip(columns,values):add('insert_field',base,lb-3,m[1]+'.'+col,[col]+([] if val.startswith('#{') else atoms(val)))
            else:
                for m in re.finditer(r'SELECT\s+COUNT\(\*\)\s+FROM\s+(\w+)\b([^)]*)',sql,re.I):
                    add('count_term',base+m.start(),base+m.end(),m[1],[m[1]]+re.findall(r"'([^']+)'",m[2]))
                for m in re.finditer(r'(?im)^\s*(WHERE|AND|JOIN)\s+[^\n]+',sql):add('filter_term',base+m.start(),base+m.end(),m[1].upper(),atoms(m[0]))
                for m in re.finditer(r'\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\s+AS\s+(\w+)',sql,re.I):add('projection_field',base+m.start(),base+m.end(),m[2],[m[2]])
                for m in re.finditer(r'\bFOR\s+(UPDATE|SHARE)\b',sql,re.I):add('lock_clause',base+m.start(),base+m.end(),m[1].upper(),['FOR',m[1].upper()])
    value=dict(format='offline_source_coverage_v1',facts=facts,unsupported=gaps,read_ids=sorted(ids),notice='Syntax only; archived data is not live source authority; no semantic or publication approval.')
    value['digest']=digest(value);return value

def scan(output,index):
    results=[]
    for fact in index['facts']:
        cited=[(i,b) for i,b in enumerate(output['blocks'],1) if fact['evidence_id'] in b['evidence_ids']]
        candidates=[]
        for i,b in cited:
            absent=[a for a in fact['anchors'] if re.search(r'(?<![\w$])'+re.escape(a)+r'(?![\w$])',b['text'],re.I) is None]
            candidates.append(dict(block=i,missing_literal_anchors=absent))
        results.append(dict(fact_id=fact['id'],kind=fact['kind'],subject=fact['subject'],state='uncited' if not cited else 'literal_anchors_found_semantics_unverified' if any(not c['missing_literal_anchors'] for c in candidates) else 'needs_review_missing_literal_anchors',candidates=candidates))
    return dict(inventory_digest=index['digest'],output_digest=digest(output),facts=results,semantic_verified=False,publication_approval=False,notice='Missing literal anchors may be paraphrases; present anchors may be negated, misleading or unrelated. This is a review checklist, never semantic scoring.')

def check_plan(output,index,plan):
    if set(plan)!={'inventory_digest','output_digest','items'} or plan['inventory_digest']!=index['digest'] or plan['output_digest']!=digest(output):raise ValueError('plan_binding')
    if not isinstance(plan['items'],list) or len(plan['items'])>256:raise ValueError('plan_limit')
    byid={f['id']:f for f in index['facts']};seen=set();pending=[]
    for item in plan['items']:
        if not isinstance(item,dict) or set(item)!={'fact_id','block','quote'} or item['fact_id'] in seen or item['fact_id'] not in byid:raise ValueError('plan_fact')
        seen.add(item['fact_id']);i=item['block']
        if type(i) is not int or not 1<=i<=len(output['blocks']):raise ValueError('plan_block')
        block=output['blocks'][i-1];quote=item['quote']
        if not isinstance(quote,str) or not quote or block['text'].count(quote)!=1 or byid[item['fact_id']]['evidence_id'] not in block['evidence_ids']:raise ValueError('plan_quote')
        pending.append(dict(fact_id=item['fact_id'],status='quoted_but_semantic_judgement_required'))
    return dict(unassigned_facts=sorted(set(byid)-seen),assigned=pending,semantic_verified=False,publication_approval=False)
