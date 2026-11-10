"""Offline prototype: explicit native functions mapped to the original ModelTurn.

No HTTP, billing or tool execution. This module is not enabled by the adapter.
Function names carry action tags; model prose and source references are untouched.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from pydantic import ValidationError

from .agent_protocol import (ModelContext, ModelTurn, ModelError, Usage, SECTION_GOAL,
    FindArguments, SearchArguments, ReadArguments, GapOutput, ClarificationOutput,
    schema_diagnostics, _safe_text)
from .learning_contract import SectionExplanation
from .source_ranges import normalize_read_ranges, read_ranges_schema, read_is_permitted

ENDPOINT = 'https://api.deepseek.com/chat/completions'
PROTOCOL = 'named_section_functions_v1'
SOURCE_NAMES = ('find_files', 'search_text', 'read_excerpt')
FINAL_NAMES = ('publish_section', 'report_gap', 'request_clarification')
_REASONS = frozenset({'context', 'ranges', 'schema', 'guidance', 'budget', 'envelope',
    'output_limit', 'function_unknown', 'mixed_actions', 'call_count', 'duplicate_id',
    'arguments_json', 'arguments_object', 'arguments_shape', 'duplicate_field',
    'read_outside_range', 'native_action_invalid'})


class Rejected(ValueError):
    def __init__(self, reason, *, diagnostics=()):
        self.reason = reason if reason in _REASONS else 'native_action_invalid'
        # Only the original fixed diagnostic whitelist may cross this boundary.
        self.diagnostics = ModelError('invalid_response', diagnostics=diagnostics).diagnostics
        super().__init__(self.reason)


def _context(value):
    try:
        checked = ModelContext.model_validate(value.model_dump())
        if checked.goal != SECTION_GOAL or checked.anchor is None or checked.anchor.topic != 'submission':
            raise ValueError
        if not checked.anchor.outline_id or not checked.anchor.section_id or not checked.anchor.section_title:
            raise ValueError
        return checked
    except (ValueError, TypeError, AttributeError):
        raise Rejected('context') from None


def _ranges(value):
    try:
        result = normalize_read_ranges(value)
        if not result: raise ValueError
        return result
    except (ValueError, TypeError):
        raise Rejected('ranges') from None


def _inline(schema):
    definitions = schema.get('$defs', {})
    def visit(node, trail=()):
        if isinstance(node, list): return [visit(item, trail) for item in node]
        if not isinstance(node, dict): return deepcopy(node)
        if '$ref' in node:
            ref = node['$ref']
            if not isinstance(ref, str) or not ref.startswith('#/$defs/'): raise Rejected('schema')
            name = ref.removeprefix('#/$defs/')
            if name not in definitions or name in trail: raise Rejected('schema')
            return visit({**definitions[name], **{k:v for k,v in node.items() if k != '$ref'}}, (*trail, name))
        return {key:visit(value, trail) for key,value in node.items() if key != '$defs'}
    return visit(schema)


def _parameters(model, omit=()):
    schema = model.model_json_schema()
    for name in omit: schema['properties'].pop(name, None)
    schema['required'] = [name for name in schema.get('required', []) if name not in omit]
    return _inline(schema)


def functions(context, ranges):
    context = _context(context); ranges = _ranges(ranges)
    reading = ReadArguments.model_json_schema()
    reading['required'] = ['path', 'start_line', 'end_line']
    reading['properties']['path'] = {'type':'string', 'enum':list(ranges)}
    reading['oneOf'] = read_ranges_schema(ranges)['oneOf']
    section = _parameters(SectionExplanation, ('kind', 'topic', 'section_id'))
    section['properties']['title']['const'] = context.anchor.section_title
    parameters = {
        'find_files': _parameters(FindArguments),
        'search_text': _parameters(SearchArguments),
        'read_excerpt': reading,
        'publish_section': section,
        'report_gap': _parameters(GapOutput, ('kind',)),
        'request_clarification': _parameters(ClarificationOutput, ('kind',)),
    }
    descriptions = {
        'find_files':'Request approved public file names; no file execution.',
        'search_text':'Request a bounded search inside approved public ranges.',
        'read_excerpt':'Request one approved immutable public source range.',
        'publish_section':'Submit title, blocks and gaps for this selected section only.',
        'report_gap':'Finish with missing evidence; do not fabricate a section.',
        'request_clarification':'Finish with a bounded clarification request.',
    }
    return [dict(type='function', function=dict(name=name, strict=False,
        description=descriptions[name], parameters=parameters[name])) for name in (*SOURCE_NAMES, *FINAL_NAMES)]


def load_guidance(base=None):
    base = Path(base) if base is not None else Path(__file__).resolve().parents[2]/'prompts'
    try:
        def read(name, limit):
            path=base/name
            if path.is_symlink() or path.resolve().parent != base.resolve(): raise ValueError
            with path.open('rb') as stream: raw=stream.read(limit+1)
            if not 1 <= len(raw) <= limit: raise ValueError
            return raw.decode('utf-8').replace('\r\n', '\n')
        task = read('submission-native-section.txt', 12000)
        style = read('learning-guide.txt', 6000)
        paragraphs=style.split('\n\n')
        old=[p for p in paragraphs if p.startswith('输出封装 json_single_object_v1：')]
        if len(old) != 1: raise ValueError
        style='\n\n'.join(p for p in paragraphs if p not in old)
        text=task+'\n\n'+style
        if len(text.encode()) > 12000 or 'json_single_object_v1' in text or 'final_output.blocks' in text: raise ValueError
        text=_safe_text(text)
        return text, sha256(text.encode()).hexdigest()
    except (OSError, UnicodeError, ValueError):
        raise Rejected('guidance') from None


def build_request(context, ranges, *, max_output_tokens=4000):
    context = _context(context)
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4000:
        raise Rejected('budget')
    guidance, _ = load_guidance()
    return dict(model='deepseek-flash', stream=False, temperature=0,
        thinking=dict(type='disabled'), max_tokens=max_output_tokens, tool_choice='required',
        tools=functions(context, ranges), messages=[
            dict(role='system', content=context.system_instruction+guidance),
            dict(role='user', content=json.dumps(context.model_dump(), ensure_ascii=False))])


def _arguments(text):
    if not isinstance(text, str) or not text.strip() or len(text)>14000: raise Rejected('arguments_json')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise Rejected('duplicate_field')
            result[key]=value
        return result
    try: value=json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError) as error:
        if isinstance(error, Rejected): raise
        raise Rejected('arguments_json') from None
    if not isinstance(value, dict): raise Rejected('arguments_object')
    return value


def decode(envelope, context, ranges, usage):
    """Decode the declared protocol once; never repair, unwrap or execute calls."""
    context = _context(context); ranges = _ranges(ranges)
    try: usage=Usage.model_validate(usage)
    except ValidationError: raise Rejected('native_action_invalid') from None
    if not isinstance(envelope, dict) or envelope.get('object')!='chat.completion' or envelope.get('model')!='deepseek-flash':
        raise Rejected('envelope')
    choices=envelope.get('choices')
    if not isinstance(choices, list) or len(choices)!=1 or not isinstance(choices[0], dict): raise Rejected('envelope')
    choice=choices[0]
    if choice.get('finish_reason')=='length': raise Rejected('output_limit')
    if choice.get('finish_reason')!='tool_calls': raise Rejected('envelope')
    message=choice.get('message')
    if (not isinstance(message, dict) or message.get('role')!='assistant'
            or message.get('content') not in (None, '') or message.get('reasoning_content') not in (None, '')):
        raise Rejected('envelope')
    calls=message.get('tool_calls')
    if not isinstance(calls, list) or not 1 <= len(calls) <= 3: raise Rejected('call_count')
    definitions={f['function']['name']:f['function']['parameters'] for f in functions(context, ranges)}
    decoded=[];ids=set()
    for call in calls:
        if (not isinstance(call, dict) or call.get('type')!='function' or not isinstance(call.get('id'), str)
                or not 1 <= len(call['id']) <= 128): raise Rejected('envelope')
        if call['id'] in ids: raise Rejected('duplicate_id')
        ids.add(call['id'])
        function=call.get('function')
        if not isinstance(function, dict) or set(function)!={'name','arguments'}: raise Rejected('envelope')
        name=function['name']
        if not isinstance(name, str) or name not in definitions: raise Rejected('function_unknown')
        args=_arguments(function['arguments']); schema=definitions[name]
        if not set(args).issubset(schema['properties']) or not set(schema.get('required', [])).issubset(args):
            raise Rejected('arguments_shape')
        decoded.append((call['id'], name, args))
    final=[value for value in decoded if value[1] in FINAL_NAMES]
    if final and len(decoded)!=1: raise Rejected('mixed_actions')
    if final:
        _,name,args=final[0]
        if name=='publish_section':
            if args['title']!=context.anchor.section_title: raise Rejected('arguments_shape')
            output=dict(args, kind='section_explanation',topic=context.anchor.topic,section_id=context.anchor.section_id)
        elif name=='report_gap': output=dict(args,kind='gap')
        else: output=dict(args,kind='clarify')
        action=dict(kind='final',final_output=output)
    else:
        for _,name,args in decoded:
            if name=='read_excerpt' and not read_is_permitted(ranges,args): raise Rejected('read_outside_range')
        action=dict(kind='tools',tool_calls=[dict(id=identifier,name=name,arguments=args) for identifier,name,args in decoded])
    try: return ModelTurn.model_validate(dict(action,usage=usage.model_dump()))
    except ValidationError as error:
        raise Rejected('native_action_invalid',diagnostics=schema_diagnostics(error)) from None
