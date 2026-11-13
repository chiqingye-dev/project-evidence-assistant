"""Offline, conservative call-syntax review prototype; not a Java type resolver.

No I/O, model, execution, publication or response repair. Trusted callers must
bind windows to actual immutable task evidence and verify lexical start state.
An unknown identifier can be callable: absence of explicit syntax proves no
absence. Registration/execution semantics require receiver implementation and
are deliberately never auto-approved here. This is NOT wired into the adapter.
"""
from dataclasses import dataclass
import re

from .agent_protocol import _safe_text
from .source_policy import require_allowed_path,SourceError

IDENT=r'[A-Za-z_$][A-Za-z0-9_$]*'
TOKEN=re.compile(IDENT+r'|(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?)[lLfFdD]?|->|::|[^\s]')
TERMS=re.compile(r'回调|callback|lambda|方法引用|登记|注册',re.I)
UNCERTAIN=re.compile(r'不能(?:确认|断言|证明)|无法(?:确认|证明)|尚未确认|是否.*(?:未知|待查)|cannot confirm|not established|unverified',re.I)
CONTRAST=re.compile(r'但是|但|然而|不过|\bbut\b|\bhowever\b',re.I)
SKIP={'if','while','for','switch','catch','synchronized','return','throw','this','super','new'}


@dataclass(frozen=True)
class Window:
    evidence_id:str
    path:str
    start_line:int
    text:str
    lexical_context_verified:bool=False


class Unsupported(ValueError):
    pass


def _tokens(source):
    if not isinstance(source,str) or len(source)>24000 or re.search(r'\\u+[0-9a-fA-F]{4}',source):
        raise Unsupported('unsupported_source')
    out=[];i=0;line=1
    while i<len(source):
        if source[i].isspace():line+=source[i]=='\n';i+=1;continue
        if source.startswith('//',i):
            end=source.find('\n',i+2);i=len(source) if end<0 else end;continue
        if source.startswith('/*',i):
            end=source.find('*/',i+2)
            if end<0:raise Unsupported('incomplete_source')
            line+=source[i:end+2].count('\n');i=end+2;continue
        if source[i] in '\"\'':
            quote=source[i];delimiter='"""' if source.startswith('"""',i) else quote
            j=i+len(delimiter);first=line
            while j<len(source):
                if source[j]=='\\':j+=2;continue
                if source.startswith(delimiter,j):break
                if delimiter!='"""' and source[j]=='\n':raise Unsupported('incomplete_source')
                j+=1
            if j>=len(source):raise Unsupported('incomplete_source')
            line+=source[i:j+len(delimiter)].count('\n')
            out.append(('<literal>',first));i=j+len(delimiter);continue
        match=TOKEN.match(source,i)
        if match is None:raise Unsupported('unsupported_source')
        word=match[0]
        if any(ord(c)>127 for c in word):raise Unsupported('unsupported_identifier')
        if len(word)==1 and not (word.isalnum() or word in '_${}()[];,.=<>!+-*/%&|^~?:@'):
            raise Unsupported('unsupported_token')
        if word[0].isdigit():word='<number>'
        out.append((word,line));i=match.end()
    return out


def _paired(tokens):
    stack=[];pairs={}
    for index,(word,_) in enumerate(tokens):
        if word in ('(','[','{'):stack.append((word,index))
        elif word in (')',']','}'):
            if not stack or stack[-1][0]!={')':'(',']':'[','}':'{'}[word]:
                raise Unsupported('unbalanced_window')
            _,left=stack.pop();pairs[left]=index
    if stack:raise Unsupported('unbalanced_window')
    return pairs


def _arguments(tokens,left,right,pairs):
    if left+1==right:return []
    args=[];start=left+1;index=start
    while index<right:
        word=tokens[index][0]
        if word in ('(','[','{'):
            if index not in pairs:raise Unsupported('incomplete_arguments')
            index=pairs[index]
        elif word==',':args.append((start,index));start=index+1
        index+=1
    args.append((start,right))
    if any(a==b for a,b in args):raise Unsupported('incomplete_arguments')
    return args


def _explicit_argument(tokens,a,b,pairs):
    # Only an arrow/reference in THIS argument, not inside a nested invocation.
    while a<b and tokens[a][0]=='(' and pairs.get(a)==b-1:a+=1;b-=1
    index=a;generic=False
    while index<b:
        word=tokens[index][0]
        if word in ('<','>'):generic=True
        if word in ('->','::') and not generic:return True
        if word in ('(','[','{'):index=pairs[index]
        index+=1
    return False


def calls(window):
    if not isinstance(window,Window):raise ValueError('invalid_window')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',window.evidence_id):raise ValueError('invalid_evidence')
    try:require_allowed_path(window.path)
    except SourceError:raise ValueError('invalid_evidence_path') from None
    if not window.path.endswith('.java'):raise ValueError('not_java_evidence')
    if type(window.start_line) is not int or not 1<=window.start_line<=1000000:raise ValueError('invalid_line')
    if window.lexical_context_verified is not True:return {'status':'context_unverified','calls':[]}
    try:
        tokens=_tokens(window.text);pairs=_paired(tokens)
        return {'status':'bounded_syntax_only','calls':_extract_calls(tokens,pairs,window)}
    except Unsupported as error:return {'status':str(error),'calls':[]}
    except ValueError:return {'status':'unsafe_label','calls':[]}


def _extract_calls(tokens,pairs,window,allowed_lines=None):
    """Internal: only emit call syntax fully inside one admitted read span.

    Whole-blob tokens supply lexical context/pairs, never outside call facts.
    """
    values=[]
    for i,(word,line) in enumerate(tokens):
        if allowed_lines and not allowed_lines[0]<=line<=allowed_lines[1]:continue
        if not re.fullmatch(IDENT,word) or word in SKIP or i+1 not in pairs or tokens[i+1][0]!='(':continue
        right=pairs[i+1]
        if allowed_lines and tokens[right][1]>allowed_lines[1]:continue
        if right+1<len(tokens) and tokens[right+1][0]=='{':continue  # declaration, conservative omission
        if i and tokens[i-1][0] in ('new','@'):continue
        qualified=i>0 and tokens[i-1][0]=='.'
        if not qualified and i and (re.fullmatch(IDENT,tokens[i-1][0]) or tokens[i-1][0] in ('>',']')):
            if tokens[i-1][0] not in ('return','throw','yield'):continue  # declaration-shaped fragment
        start=i
        while start>=2 and tokens[start-1][0]=='.' and re.fullmatch(IDENT,tokens[start-2][0]):start-=2
        if allowed_lines and tokens[start][1]<allowed_lines[0]:continue
        name=''.join(t[0] for t in tokens[start:i+1]);_safe_text(name)
        arguments=_arguments(tokens,i+1,right,pairs)
        limited=any(word in ('<','>') for word,_ in tokens[i+2:right])
        explicit=not limited and any(_explicit_argument(tokens,a,b,pairs) for a,b in arguments)
        values.append(dict(callee=name,name=word,evidence_id=window.evidence_id,path=window.path,
            start_line=window.start_line+line-1,end_line=window.start_line+tokens[right][1]-1,
            argument_count=None if limited else len(arguments),passing='explicit_callable' if explicit else 'unknown',
            syntax_scope='unsupported_angle_form' if limited else 'bounded_arguments',
            registration='unverified',execution_timing='unverified',site_index=len(values)+1))
        if len(values)>64:raise Unsupported('call_limit')
    return values


def _uncertainty_only(unit,first_term):
    # A later caveat cannot retract an earlier assertion. Factual negation such
    # as "does not register" is not uncertainty and is not auto-approved.
    # A bounded noun-phrase gap states what has NOT been read; it does not say
    # a callback was passed/registered. Do not extend this to factual negation.
    compact=re.sub(r'[\s`*]','',unit)
    # Whole-unit question/unknown-list grammar only. A question prefix must
    # never exempt an appended assertion or factual negation from review.
    name=IDENT+r'(?:\.'+IDENT+r')*'
    if re.fullmatch(name+r'(?:的接收方)?是否(?:登记|注册)回调(?:[、及和]何时执行)?[?？]?',compact):
        return True
    if re.fullmatch(r'\s*(?:Does|Did|Will|Can)\s+'+name+r'\s+(?:register|execute)\s+(?:a\s+|the\s+)?callback\s*\?\s*',unit,re.I):
        return True
    if re.fullmatch(r'(?:未知项|未核实项|待核实项)[：:]'+name+r'的?(?:登记|注册)回调[与及和]执行时机[?？]?',compact):
        return True
    if re.fullmatch(r'回调(?:登记|注册)[与及和]执行(?:时机|时间)(?:未确认|尚未核实|未知|待查)',compact):
        return True
    if re.fullmatch(IDENT+r'(?:\.'+IDENT+r')*的?(?:接收方)?实现[与及和]回调执行(?:时机|时间)(?:未读|尚未读取|未确认|未知|待查)',compact):
        return True
    matches=list(UNCERTAIN.finditer(unit))
    question=re.search(r'是否|\bwhether\b',unit,re.I)
    return bool(matches and (any(m.start()<first_term for m in matches) or
        question and question.start()<first_term) and not CONTRAST.search(unit))


def audit(blocks,windows):
    """Conservative phrase-triggered review, NOT general semantic acceptance.

    Trusted window extraction and broad paraphrase coverage remain integration
    work. Unsupported/ambiguous evidence requests review, never silent repair.
    """
    if not isinstance(blocks,list) or not 1<=len(blocks)<=12 or not isinstance(windows,list) or len(windows)>16:
        raise ValueError('review_limit')
    sources={}
    for window in windows:
        if window.evidence_id in sources:raise ValueError('duplicate_evidence')
        sources[window.evidence_id]=calls(window)
    return _audit_sources(blocks,sources)


def _audit_sources(blocks,sources):
    if not isinstance(blocks,list) or not 1<=len(blocks)<=12:raise ValueError('review_limit')
    findings=[];evaluated=[];chars=0
    for block_index,block in enumerate(blocks,1):
        text=block.get('text');ids=block.get('evidence_ids')
        if not isinstance(text,str) or not isinstance(ids,list) or any(not isinstance(v,str) for v in ids):raise ValueError('invalid_block')
        chars+=len(text)
        if chars>14000:raise ValueError('review_limit')
        heading=block.get('heading','')
        if not isinstance(heading,str):raise ValueError('invalid_block')
        chars+=len(heading)
        if chars>14000:raise ValueError('review_limit')
        units=[(field,unit) for field,content in (('heading',heading),('text',text))
               for unit in re.split(r'[\n。；;，,]',content)]
        for unit_index,(field,unit) in enumerate(units,1):
            term=TERMS.search(unit)
            if term is None:continue
            if len(findings)+len(evaluated)>=256:raise ValueError('review_limit')
            item={'block':block_index,'field':field,'unit':unit_index}
            if _uncertainty_only(unit,term.start()):
                evaluated.append(dict(item,result='uncertainty_only'));continue
            candidates=[]
            mentioned=re.findall(IDENT+r'(?:\.'+IDENT+r')*',unit)
            for identifier in ids:
                for call in sources.get(identifier,{}).get('calls',[]):
                    if any(name==call['callee'] or '.' not in name and name==call['name'] for name in mentioned):
                        candidates.append(call)
            unique={(c['evidence_id'],c.get('site_index'),c['start_line'],c['callee']):c for c in candidates}
            if len(unique)!=1:
                findings.append(dict(item,reason='missing_or_ambiguous_call_evidence'));continue
            call=next(iter(unique.values()))
            item.update(callee=call['callee'],evidence_id=call['evidence_id'],source_start=call['start_line'])
            if re.search(r'登记|注册|register',unit,re.I):
                findings.append(dict(item,reason='receiver_registration_unverified'))
            elif call['passing']!='explicit_callable':
                findings.append(dict(item,reason='passing_callable_unproven'))
            elif re.search(r'执行|触发|必然|立即|同步|异步|提交后|\b(?:executes?|invokes?|runs?|always|immediately)\b',unit,re.I):
                findings.append(dict(item,reason='claim_exceeds_argument_syntax'))
            elif not re.search(r'传入|作为.*参数|passed?\b',unit,re.I):
                findings.append(dict(item,reason='claim_exceeds_argument_syntax'))
            else:evaluated.append(dict(item,result='explicit_argument_only_timing_unverified'))
    return dict(status='needs_review' if findings else 'within_prototype_scope',findings=findings,evaluated=evaluated,
        semantic_verified=False,output_modified=False,publication_performed=False,publication_approval=False)


def audit_output(output,windows):
    """Cover section title, block heading/text (including diagrams) and gaps."""
    if not isinstance(windows,list) or len(windows)>16:raise ValueError('review_limit')
    sources={}
    for window in windows:
        if window.evidence_id in sources:raise ValueError('duplicate_evidence')
        sources[window.evidence_id]=calls(window)
    return _audit_output_sources(output,sources)


def _audit_output_sources(output,sources):
    if not isinstance(output,dict) or not isinstance(output.get('title'),str):raise ValueError('invalid_output')
    gaps=output.get('gaps',[])
    if not isinstance(gaps,list) or len(gaps)>12 or any(not isinstance(g,str) for g in gaps):raise ValueError('invalid_output')
    result=_audit_sources(output.get('blocks'),sources)
    for field,text in [('title',output['title']),*[(f'gaps.{i}',g) for i,g in enumerate(gaps,1)]]:
        extra=_audit_sources([dict(text=text,evidence_ids=[])],sources)
        for key in ('findings','evaluated'):
            result[key].extend(dict(item,field=field,block=None) for item in extra[key])
    if len(result['findings'])+len(result['evaluated'])>256:raise ValueError('review_limit')
    result['status']='needs_review' if result['findings'] else 'within_prototype_scope'
    return result
