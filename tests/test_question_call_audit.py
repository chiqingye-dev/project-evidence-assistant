import pytest
from project_evidence.call_claim_contract import review
from test_section_review import collected
from test_call_binding import output


QUESTIONS=['afterCommit 是否登记回调？','Does afterCommit register a callback?',
           '未知项：afterCommit 的登记回调与执行时机。',
           '`afterCommit` 是否登记回调、何时执行', '回调登记与执行时机未确认']
ASSERTIONS=['afterCommit 不登记回调。','afterCommit 确实登记回调。',
            'afterCommit 是否登记回调？afterCommit 确实登记回调。',
            'Does afterCommit register a callback? It does register a callback.',
            '未知项：afterCommit 确实登记回调。','afterCommit 是否登记回调已经确认。',
            'afterCommit 是否登记回调，但是 afterCommit 已经登记回调。',
            'afterCommit 是否登记回调、何时执行？afterCommit 确实登记回调。',
            '回调登记与执行时机未确认但 afterCommit 确实登记回调。']


@pytest.mark.parametrize('field',['text','heading','title','gap'])
@pytest.mark.parametrize('text',QUESTIONS+ASSERTIONS)
def test_whole_question_or_noun_gap_never_exempts_appended_assertion(field,text):
    c,refs=collected();value=output('这是普通静态调用。')
    if field=='text':value['blocks'][0]['text']=text
    if field=='heading':value['blocks'][0]['heading']=text
    if field=='title':value['title']=text
    if field=='gap':value['gaps']=[text]
    report=review(value,c.index(refs),[])
    assert bool(report['claim_findings'] or report['bounded_body_audit']['findings']) is (text in ASSERTIONS)
    assert not report['semantic_verified'] and not report['publication_approval']
