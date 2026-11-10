"""Offline boundary/regression checks; not a Java compiler or model score."""
from copy import deepcopy
import pytest
from project_evidence.call_evidence import Window,calls,audit,audit_output

PATH='src/Example.java'


def window(text,identifier='piece-one',*,trusted=True):
    return Window(identifier,PATH,10,text,trusted)


def block(text,ids=None,category='mechanism'):
    return {'text':text,'evidence_ids':['piece-one'] if ids is None else ids,'category':category}


@pytest.mark.parametrize('source,callee,passing,count',[
    ('Events.send("created", id, 0);','Events.send','unknown',3),
    ('submit(() -> work());','submit','explicit_callable',1),
    ('submit((value) -> { work(value); });','submit','explicit_callable',1),
    ('submit(this::work);','submit','explicit_callable',1),
    ('submit((this::work));','submit','explicit_callable',1),
    ('submit(callback);','submit','unknown',1),
    ('submit(new Runnable() { public void run() {} });','submit','unknown',1),
    ('outer(inner(() -> work()));','outer','unknown',1),
    ('submit(Map.<String, Long>of());','submit','unknown',None),
    ('if (ready) send(value);','send','unknown',1),
])
def test_only_explicit_argument_syntax_is_positive_evidence(source,callee,passing,count):
    found=calls(window(source));assert found['status']=='bounded_syntax_only'
    call=next(c for c in found['calls'] if c['callee']==callee)
    assert call['passing']==passing and call['argument_count']==count
    assert call['registration']==call['execution_timing']=='unverified'


def test_comments_strings_text_blocks_do_not_create_callback_evidence():
    source='// fake(() -> run());\nString s="fake(() -> run())";\nString t="""\nfake(this::work);\n""";\nreal(1);'
    found=calls(window(source))
    assert [c['callee'] for c in found['calls']]==['real']
    assert found['calls'][0]['start_line']==15


@pytest.mark.parametrize('source',['broken(', 'send("unterminated);', '/* no end', r'send(\u0061);','send(中文);'])
def test_unsupported_or_incomplete_lexical_evidence_cannot_approve(source):
    proof=calls(window(source));assert proof['status']!='bounded_syntax_only' and not proof['calls']
    assert audit([block('send 传入回调。')],[window(source)])['status']=='needs_review'


def test_unknown_lexical_context_is_not_trusted_by_default():
    proof=calls(Window('piece-one',PATH,10,'send(() -> work());'))
    assert proof['status']=='context_unverified'
    assert audit([block('send 传入回调。')],[window('send(() -> work());',trusted=False)])['status']=='needs_review'


@pytest.mark.parametrize('text',[
    'afterCommit 是传入回调的调用点。',
    '```mermaid\nA["afterCommit 登记（传入回调，何时执行待查）"]\n```',
    'afterCommit 传入回调，但接收方实现未读。',
    'afterCommit does not register a callback.',
    'afterCommit 不登记回调。',
    '是否传入回调未知，afterCommit 确实登记回调。',
])
def test_ordinary_value_call_and_later_caveats_do_not_support_callback_claim(text):
    result=audit([block(text)],[window('Events.afterCommit("created", id, taskId, eventId, "QUEUED", 0);')])
    assert result['status']=='needs_review' and result['findings']
    assert result['semantic_verified'] is False and result['output_modified'] is False


@pytest.mark.parametrize('text',[
    'afterCommit 是否传入回调尚未确认。',
    '未读 afterCommit 实现，不能断言回调何时执行。',
    '不能确认 afterCommit 是否登记回调。',
    'It is unverified whether afterCommit receives a callback.',
    'CommittedJudgingEvents.afterCommit 的接收方实现与回调执行时机未读',
    'other.finish 的实现及回调执行时间尚未读取',
])
def test_clear_uncertainty_is_not_rejected_as_a_positive_assertion(text):
    result=audit([block(text,category='gap')],[window('Events.afterCommit(value);')])
    assert result['status']=='within_prototype_scope' and not result['findings']


def test_noun_phrase_gap_cannot_hide_appended_positive_assertion():
    text='afterCommit 的接收方实现与回调执行时机未读，但 afterCommit 确实登记回调。'
    assert audit([block(text)],[window('Events.afterCommit(value);')])['status']=='needs_review'


def test_supported_explicit_passing_still_does_not_prove_registration():
    sources=[window('send(() -> work());')]
    assert audit([block('send 传入回调。')],sources)['status']=='within_prototype_scope'
    assert audit([block('send 登记回调。')],sources)['status']=='needs_review'


def test_callback_from_another_call_or_unrelated_evidence_cannot_be_borrowed():
    sources=[window('other(() -> work()); Events.afterCommit(id);')]
    assert audit([block('afterCommit 传入回调。')],sources)['status']=='needs_review'
    assert audit([block('other 传入回调。',ids=['unread'])],sources)['status']=='needs_review'


def test_qualified_receiver_cannot_borrow_another_receivers_callable_argument():
    sources=[window('first.send(() -> work());')]
    assert audit([block('second.send 传入回调。')],sources)['status']=='needs_review'
    assert audit([block('first.send 传入回调。')],sources)['status']=='within_prototype_scope'


def test_passing_syntax_does_not_establish_callback_execution():
    result=audit([block('send 传入回调并立即执行。')],[window('send(() -> work());')])
    assert result['status']=='needs_review' and result['publication_approval'] is False


def test_within_scope_is_never_publication_or_semantic_approval():
    result=audit([block('send 传入回调。')],[window('send(() -> work());')])
    assert result['status']=='within_prototype_scope'
    assert result['semantic_verified'] is result['publication_approval'] is False


def test_repeated_unqualified_callee_is_ambiguous_but_distinct_caller_not_merged():
    result=audit([block('send 传入回调。')],[window('a.send(() -> work()); b.send(value);')])
    assert result['findings'][0]['reason']=='missing_or_ambiguous_call_evidence'


def test_gap_category_does_not_launder_positive_claim_and_input_is_untouched():
    blocks=[block('afterCommit 传入回调。',category='gap')];before=deepcopy(blocks)
    result=audit(blocks,[window('Events.afterCommit(value);')])
    assert result['status']=='needs_review' and blocks==before and not result['publication_performed']


def test_method_declaration_is_not_an_invocation():
    assert not calls(window('void send(Runnable task) { }'))['calls']
    assert not calls(window('void send(Runnable task);'))['calls']


@pytest.mark.parametrize('field',['heading','title','gap'])
def test_positive_claim_cannot_be_hidden_outside_block_text(field):
    output={'title':'事务边界','blocks':[block('这是普通调用。')],'gaps':[]}
    if field=='heading':output['blocks'][0]['heading']='afterCommit 登记回调'
    if field=='title':output['title']='afterCommit 登记回调'
    if field=='gap':output['gaps']=['afterCommit 传入回调']
    saved=deepcopy(output)
    assert audit_output(output,[window('Events.afterCommit(value);')])['status']=='needs_review'
    assert output==saved


@pytest.mark.parametrize('problem',['duplicate','path','line','id','size','blocks'])
def test_input_boundaries(problem):
    sources=[window('send(1);')];blocks=[block('send 传入回调。')]
    if problem=='duplicate':sources*=2
    if problem=='path':sources=[Window('piece-one','../private.java',1,'send(1);',True)]
    if problem=='line':sources=[Window('piece-one',PATH,True,'send(1);',True)]
    if problem=='id':sources=[Window('bad id',PATH,1,'send(1);',True)]
    if problem=='size':blocks=[block('x'*14001)]
    if problem=='blocks':blocks*=13
    with pytest.raises(ValueError):audit(blocks,sources)
