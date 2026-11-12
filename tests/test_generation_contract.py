from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest
from project_evidence.section_semantic_review import ReviewContract
from project_evidence.source_coverage import build
from project_evidence.source_policy import SourceError
from test_source_coverage_binding import collected
from test_call_binding import output
from project_evidence.generation_contract import (planning_packet as _packet, planning_context as _context,
                               prepare as _prepare, complete, final_report,
                               BoundPlan, PlannedFinal, PublicBrief)


def public(spec):
    # Synthetic fixtures only. Real integration must derive this from public goal.
    return PublicBrief(tuple(f'Public authoring objective {i}.' for i in range(1, len(spec.required)+1)))


def planning_packet(b, c, refs, spec):
    return _packet(b, c, refs, spec, public(spec))


def prepare(b, c, refs, spec, draft):
    return _prepare(b, c, refs, spec, public(spec), draft)


def planning_context(ctx, b, c, refs, spec, **kw):
    return _context(ctx, b, c, refs, spec, public(spec), **kw)


def fixture(required=('Explain the stored state.',)):
    c, refs, _ = collected()
    contract = ReviewContract('a' * 64, 'FIXTURE', c.scope.public_url, c.scope.commit, tuple(required))
    bound = build(c, refs, ('writes',))
    packet = planning_packet(bound, c, refs, contract)
    target = next(k for k in packet['targets'] if k.startswith('f:'))
    draft = dict(criteria=[dict(number=i, targets=[target], gap='') for i in range(1, len(required)+1)])
    return c, refs, bound, contract, target, draft


def body(text='写入时将任务状态设为 WAIT。', citations=('e1',)):
    value = output(text)
    value['blocks'][0]['evidence_ids'] = list(citations)
    if not citations:
        value['blocks'][0]['support'] = 'candidate'
    return value


def claim(target, **changes):
    value = dict(number=1, target=target, field='block:1:text',
                 quote='写入时将任务状态设为 WAIT。', kind='explanation')
    return dict(value, **changes)


def ready(text=None, citations=('e1',)):
    c, refs, bound, contract, target, draft = fixture()
    plan = prepare(bound, c, refs, contract, draft)
    final = complete(plan, c, contract, body(text or '写入时将任务状态设为 WAIT。', citations), refs)
    return c, contract, target, plan, final


def test_valid_plan_and_exact_final_span_are_only_identity_not_semantic_approval():
    c, spec, target, plan, final = ready()
    before = final.view()
    result = final_report(plan, final, c, spec, [claim(target)])
    assert result['structural_obligations_recorded'] and not result['findings']
    assert result['status'] == 'needs_independent_review'
    assert not result['publication_approval'] and not result['semantic_verified']
    assert not result['publication_performed'] and not result['output_modified']
    assert final.view() == before
    assert result['output_digest'] == sha256(json.dumps(before['output'], ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert result['bound_claims'][0]['start'] == 0


@pytest.mark.parametrize('case', ['missing', 'duplicate', 'bool', 'invented', 'empty', 'extra', 'private', 'too_large'])
def test_bad_or_self_approving_plans_cannot_be_bound(case):
    c, refs, b, spec, target, draft = fixture(('One', 'Two'))
    if case == 'missing': draft['criteria'].pop()
    if case == 'duplicate': draft['criteria'][1]['number'] = 1
    if case == 'bool': draft['criteria'][0]['number'] = True
    if case == 'invented': draft['criteria'][0]['targets'] = ['f:999']
    if case == 'empty': draft['criteria'][0]['targets'] = []
    if case == 'extra': draft['publication_approval'] = True
    if case == 'private': draft['criteria'][0]['gap'] = 'C:/Users/private/config'
    if case == 'too_large': draft['criteria'][0]['gap'] = 'x' * 20000
    with pytest.raises(ValueError): prepare(b, c, refs, spec, draft)


@pytest.mark.parametrize('case', ['view', 'foreign_collector', 'foreign_contract', 'tampered_blob', 'late_plan'])
def test_current_source_and_before_freeze_authority_are_required(case):
    c, refs, b, spec, target, draft = fixture()
    if case == 'view': b = b.view()
    if case == 'foreign_collector': c = collected()[0]
    if case == 'foreign_contract': spec = replace(spec, commit='b' * 40)
    if case == 'tampered_blob': c._blobs[refs[0]['path']] += b'\n'
    if case == 'late_plan': c.freeze(body(), refs)
    with pytest.raises((ValueError, SourceError)): prepare(b, c, refs, spec, draft)


def test_missing_body_and_only_ids_do_not_satisfy_explanation_presence():
    c, spec, target, plan, final = ready(text='f:0, c:0, e1')
    result = final_report(plan, final, c, spec, [])
    assert result['findings'][0]['reason'] == 'missing_body_claim'
    result = final_report(plan, final, c, spec, [claim(target, quote='f:0, c:0, e1')])
    assert 'reference_list_only' in {x['reason'] for x in result['findings']}


@pytest.mark.parametrize('case', ['absent_quote', 'ambiguous_quote', 'unknown_target', 'wrong_criterion',
                                 'duplicate', 'unwritten_field', 'extra', 'view', 'other_plan', 'other_contract'])
def test_wrong_body_locations_or_identities_cannot_gain_coverage(case):
    c, refs, b, spec, target, draft = fixture()
    plan = prepare(b, c, refs, spec, draft)
    other = prepare(b, c, refs, spec, draft)
    final = complete(plan, c, spec, body('same same' if case == 'ambiguous_quote' else
                                       '写入时将任务状态设为 WAIT。'), refs)
    claims = [claim(target)]
    if case == 'absent_quote': claims[0]['quote'] = 'unwritten'
    if case == 'ambiguous_quote': claims[0]['quote'] = 'same'
    if case == 'unknown_target': claims[0]['target'] = 'f:999'
    if case == 'wrong_criterion': claims[0]['number'] = 2
    if case == 'duplicate': claims *= 2
    if case == 'unwritten_field': claims[0]['field'] = 'block:2:text'
    if case == 'extra': claims[0]['verdict'] = 'pass'
    if case == 'view': final = final.view()
    if case == 'other_plan': plan = other
    if case == 'other_contract': spec = replace(spec, required=('Changed',))
    with pytest.raises(ValueError): final_report(plan, final, c, spec, claims)


def test_unread_relationship_and_declared_unknown_are_never_positive_coverage():
    for kind in ('relationship', 'unknown'):
        c, spec, target, plan, final = ready(text='接收方登记并执行回调。')
        result = final_report(plan, final, c, spec,
            [claim(target, quote='接收方登记并执行回调。', kind=kind)])
        assert result['findings'] and not result['structural_obligations_recorded']
        assert not result['publication_approval']


def test_explicit_gap_cannot_silently_disappear_or_turn_into_supported_fact():
    c, refs, b, spec, target, draft = fixture()
    draft['criteria'][0] = dict(number=1, targets=[], gap='Receiver implementation unread.')
    plan = prepare(b, c, refs, spec, draft)
    final = complete(plan, c, spec, body('尚未读取接收方实现。'), refs)
    assert final_report(plan, final, c, spec, [])['findings'][0]['reason'] == 'missing_body_claim'
    result = final_report(plan, final, c, spec,
        [claim(None, quote='尚未读取接收方实现。', kind='explanation')])
    assert 'source_gap_cannot_become_assertion' in {x['reason'] for x in result['findings']}


def test_unplanned_syntax_is_visible_and_is_not_automatically_a_required_fact():
    c, refs, b, spec, target, draft = fixture()
    packet = planning_packet(b, c, refs, spec)
    plan = prepare(b, c, refs, spec, draft)
    assert set(plan.view()['unplanned_targets']) == set(packet['targets']) - {target}
    assert plan.view()['unplanned_targets']  # Existing call rows are explicitly unassigned.
    assert not plan.view()['generation_permission']


def test_plan_and_final_are_opaque_immutable_and_final_is_bound_once():
    c, refs, b, spec, target, draft = fixture()
    plan = prepare(b, c, refs, spec, draft)
    before = plan.view(); draft['criteria'].clear()
    assert plan.view() == before
    with pytest.raises(ValueError): BoundPlan(before, c)
    with pytest.raises(ValueError): plan._text = '{}'
    final = complete(plan, c, spec, body(), refs)
    with pytest.raises(ValueError): PlannedFinal(final._candidate, plan)
    with pytest.raises(ValueError): final._candidate = None
    with pytest.raises(SourceError): complete(plan, c, spec, body('replacement'), refs)


def test_arbitrary_plausible_prose_can_pass_structure_but_never_become_truth():
    # Deliberately wrong meaning: the prototype must not report semantic success.
    c, spec, target, plan, final = ready(text='写入时将任务状态设为 DONE。')
    result = final_report(plan, final, c, spec,
        [claim(target, quote='写入时将任务状态设为 DONE。')])
    assert result['structural_obligations_recorded']
    assert not result['semantic_verified'] and not result['publication_approval']


@pytest.mark.parametrize('case', ['title', 'uncited', 'source_changed'])
def test_headings_citations_and_current_source_remain_distinct_checks(case):
    c, spec, target, plan, final = ready(citations=() if case == 'uncited' else ('e1',))
    data = claim(target)
    if case == 'title': data.update(field='title', quote='调用边界')
    if case == 'source_changed':
        c._blobs[next(iter(c._blobs))] += b'\n'
        with pytest.raises(ValueError): final_report(plan, final, c, spec, [data])
        return
    reasons = {x['reason'] for x in final_report(plan, final, c, spec, [data])['findings']}
    assert ('explanation_not_in_body' if case == 'title' else 'source_not_cited') in reasons


def test_planning_frames_retain_observations_and_measure_the_added_plan():
    c, refs, ctx = collected()
    spec = ReviewContract('a'*64, 'FIXTURE', c.scope.public_url, c.scope.commit, ('Explain state.',))
    b = build(c, refs, ('writes',)); before = deepcopy(ctx)
    frame = planning_context(ctx, b, c, refs, spec)
    plan = prepare(b, c, refs, spec, dict(criteria=[dict(number=1, targets=['f:0'], gap='')]))
    after = planning_context(ctx, b, c, refs, spec, plan=plan)
    assert ctx == before and frame['observations'] == before['observations']
    assert after['history'][-1]['host_generation_coverage']['plan_digest'] == plan.view()['plan_digest']
    with pytest.raises(SourceError): planning_context(ctx, b, c, refs, spec, max_chars=1)
    ctx['padding'] = 'x'*24000
    with pytest.raises(SourceError): planning_context(ctx, b, c, refs, spec)


@pytest.mark.parametrize('case', ['duplicate', 'foreign_view', 'wrong_excerpt', 'raise_cap', 'other_facets'])
def test_context_additions_cannot_replace_source_guards_or_raise_limits(case):
    c, refs, ctx = collected()
    spec = ReviewContract('a'*64, 'FIXTURE', c.scope.public_url, c.scope.commit, ('Explain state.',))
    b = build(c, refs, ('writes',)); cap = 24000; plan = None
    if case == 'duplicate': ctx = planning_context(ctx, b, c, refs, spec)
    if case == 'foreign_view':
        ctx['history'].append(c.coverage_context(refs, ('writes',)))
        ctx['history'][-1]['host_source_coverage']['coverage_digest'] = '0'*64
    if case == 'wrong_excerpt': ctx['observations'][0]['result']['text'] = 'different'
    if case == 'raise_cap': cap = 24001
    if case == 'other_facets':
        plan = prepare(b, c, refs, spec, dict(criteria=[dict(number=1, targets=['f:0'], gap='')]))
        b = build(c, refs, ('branches',))
    with pytest.raises((ValueError, SourceError)):
        planning_context(ctx, b, c, refs, spec, plan=plan, max_chars=cap)


def test_existing_exact_combined_entry_can_be_used_without_duplication():
    c, refs, ctx = collected()
    spec = ReviewContract('a'*64, 'FIXTURE', c.scope.public_url, c.scope.commit, ('Explain state.',))
    b = build(c, refs, ('writes',)); ctx['history'].append(c.coverage_context(refs, ('writes',)))
    frame = planning_context(ctx, b, c, refs, spec)
    assert sum('host_source_coverage' in row for row in frame['history']) == 1


def test_whitespace_is_not_a_body_explanation():
    c, spec, target, plan, final = ready(text='a b')
    with pytest.raises(ValueError): final_report(plan, final, c, spec, [claim(target, quote=' ')])


def test_private_answer_text_is_never_in_planning_packet_or_model_frame():
    c, refs, ctx = collected()
    spec = ReviewContract('a'*64, 'FIXTURE', c.scope.public_url, c.scope.commit,
                          ('PRIVATE_ANSWER_WITH_CONSTANTS', 'SECOND_PRIVATE_EXPECTED_RESULT'))
    b = build(c, refs, ('writes',))
    brief = PublicBrief(('Describe the requested mechanism from the allowed source.',))
    packet = _packet(b, c, refs, spec, brief)
    assert packet['public_objectives'] == list(brief.objectives)
    frame = _context(ctx, b, c, refs, spec, brief)
    text = json.dumps([packet, frame], ensure_ascii=False)
    assert all(answer not in text for answer in spec.required)
    # Authoring objectives are independent of the number of private criteria.
    plan = _prepare(b, c, refs, spec, brief,
                    dict(criteria=[dict(number=1, targets=['f:0'], gap='')]))
    assert len(plan.view()['criteria']) == 1 and len(spec.required) == 2


@pytest.mark.parametrize('case', ['json', 'blank', 'duplicate', 'private_path', 'changed'])
def test_public_brief_is_separate_host_input_and_cannot_change_after_plan(case):
    c, refs, ctx = collected()
    spec = ReviewContract('a'*64, 'FIXTURE', c.scope.public_url, c.scope.commit, ('Private.',))
    b = build(c, refs, ('writes',))
    with pytest.raises(ValueError):
        if case == 'json': _packet(b, c, refs, spec, {'objectives': ['Goal']})
        if case == 'blank': PublicBrief((' ',))
        if case == 'duplicate': PublicBrief(('Goal', 'Goal'))
        if case == 'private_path': PublicBrief(('C:/Users/private/config',))
        if case == 'changed':
            plan = _prepare(b, c, refs, spec, PublicBrief(('Goal',)),
                            dict(criteria=[dict(number=1, targets=['f:0'], gap='')]))
            _context(ctx, b, c, refs, spec, PublicBrief(('Changed',)), plan=plan)
