"""Host-only accepted-event collection, immutable hold and atomic storage."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest
from project_evidence.section_review import AcceptedReads, ReviewPolicy, HeldReviewStore, FrozenCandidate
from project_evidence.source_policy import SourceError
from test_call_binding import fixture,output
from test_learning_core import setup


def collected():
    scope,refs,chunks,blobs=fixture()
    c=AcceptedReads(scope);c._blobs=blobs
    ref=refs[0];saved=dict(ref,id=ref['evidence_id'],text=chunks[ref['evidence_id']]['text'])
    c.saved(saved,saved)
    c.accept(dict(type='tool_feedback_accepted',details=dict(tool='read_excerpt',status='ok',evidence_id=ref['evidence_id'])))
    return c,refs


def test_raw_saved_return_cannot_substitute_for_accepted_feedback():
    scope,refs,chunks,blobs=fixture();c=AcceptedReads(scope);c._blobs=blobs
    ref=refs[0];saved=dict(ref,id=ref['evidence_id'],text=chunks[ref['evidence_id']]['text'])
    c.saved(saved,saved)
    with pytest.raises(SourceError):c.freeze(output(),refs)


@pytest.mark.parametrize('case',['foreign_event','duplicate_event','different_saved','different_final','missing_final','extra_final'])
def test_wrong_or_replayed_event_and_outcome_do_not_mint_authority(case):
    c,refs=collected()
    with pytest.raises((SourceError,ValueError)):
        if case in ('foreign_event','duplicate_event'):
            c.accept(dict(type='tool_feedback_accepted',details=dict(tool='read_excerpt',status='ok',evidence_id='unread' if case=='foreign_event' else refs[0]['evidence_id'])))
        elif case=='different_saved':
            saved=dict(refs[0],id=refs[0]['evidence_id'],text='changed');c.saved(saved,dict(saved,text='different'))
        else:
            if case=='different_final':refs[0]['sha256']='0'*64
            if case=='missing_final':refs=[]
            if case=='extra_final':refs.append(dict(refs[0],evidence_id='other'))
            c.freeze(output(),refs)


def test_freeze_seals_original_output_and_never_approves_even_supported_syntax():
    c,refs=collected();body=output('send(() -> work()) 是已读调用。');before=deepcopy(body)
    frozen=c.freeze(body,refs);view=frozen.view()
    assert view['output']==before and view['status']=='needs_review'
    assert view['audit']['status']=='syntax_supported_only'
    assert view['semantic_verified'] is view['publication_approval'] is False
    body['title']='changed';view['output']['title']='changed'
    assert frozen.view()['output']==before
    with pytest.raises(SourceError):c.freeze(before,refs)
    with pytest.raises(SourceError):c.accept({})
    with pytest.raises(ValueError):frozen._text='forged'
    with pytest.raises(ValueError):FrozenCandidate(json.dumps(view))


def test_unsupported_claim_is_saved_unchanged_with_reasons():
    c,refs=collected();body=output('send 登记并立即执行回调。')
    frozen=c.freeze(body,refs).view()
    assert frozen['output']==body
    assert frozen['audit']['status']=='needs_review'
    assert frozen['audit']['claim_findings'] and frozen['audit']['bounded_body_audit']['findings']


def test_size_stop_does_not_mark_collector_frozen_or_truncate():
    c,refs=collected()
    with pytest.raises(SourceError) as e:c.freeze(output(),refs,max_bytes=1)
    assert e.value.code=='budget_exhausted'
    assert c.freeze(output(),refs).view()['output']==output()


@pytest.mark.parametrize('case',['commit','repository','topic','section'])
def test_host_policy_is_bound_to_selected_project_and_section(case):
    scope,*_=fixture();policy=ReviewPolicy(scope.public_url,scope.commit,scope.topic,scope.section_id,scope.files)
    record=SimpleNamespace(id=scope.project_id,public_url=scope.public_url,commit=scope.commit)
    anchor=SimpleNamespace(topic=scope.topic,section_id=scope.section_id)
    if case=='commit':record.commit='b'*40
    if case=='repository':record.public_url='https://github.com/other/repo'
    if case=='topic':anchor.topic='query'
    if case=='section':anchor.section_id='other'
    with pytest.raises(SourceError):policy.scope(record,scope.task_id,anchor)


def stored_candidate(env):
    projects,record,tasks,lessons,eid,_,_=env
    task=tasks.create(record,'scripted',{},kind='section_explanation',inputs={'outline_id':'fixture-outline','section_id':'entry'})
    c,refs=collected()
    # Current task/project identities are supplied by the trusted host fixture.
    c.scope=replace(c.scope,task_id=task['id'],project_id=str(record.id),commit=record.commit)
    for _,chunk in c._accepted.values():chunk.update(task_id=task['id'],project_id=str(record.id))
    candidate=c.freeze(output(),refs)
    return task,candidate


def test_atomic_hold_is_owned_append_only_and_survives_reopen(tmp_path):
    env=setup(tmp_path);projects,record,tasks,lessons,*_=env
    store=HeldReviewStore(projects);task,candidate=stored_candidate(env);before=lessons.list(record)
    assert tasks.update(record.id,task['id'],status='needs_review',result=candidate.view()['output'],
                        hold=lambda connection:store.save(connection,candidate))
    assert tasks.get(record.id,task['id'])['status']=='needs_review'
    assert lessons.list(record)==before
    assert HeldReviewStore(projects,create=False).get(record.id,task['id'])==candidate.view()
    assert store.get('other-project',task['id']) is None
    assert not tasks.update(record.id,task['id'],status='completed',publish=lambda c:pytest.fail('release'))
    assert not hasattr(store,'approve') and not hasattr(store,'release')
    projects.delete(record.id)
    assert store.get(record.id,task['id']) is None


@pytest.mark.parametrize('case',['store_error','result_changed','json_forgery','wrong_terminal','cancelled','version_changed'])
def test_failed_hold_rolls_back_without_publishing_or_overwriting_history(tmp_path,case):
    env=setup(tmp_path);projects,record,tasks,lessons,*_=env;store=HeldReviewStore(projects)
    task,candidate=stored_candidate(env);body=candidate.view()['output'];before=lessons.list(record)
    if case=='result_changed':body['title']='changed'
    if case=='cancelled':tasks.update(record.id,task['id'],status='cancelled')
    if case=='version_changed':
        with projects._connection() as db:db.execute('UPDATE projects SET commit_sha=? WHERE id=?',('b'*40,str(record.id)))
    def hold(db):
        if case=='store_error':raise RuntimeError('synthetic storage failure')
        store.save(db,candidate.view() if case=='json_forgery' else candidate)
    if case in ('cancelled','version_changed'):
        assert not tasks.update(record.id,task['id'],status='needs_review',result=body,hold=hold)
    else:
        with pytest.raises((ValueError,RuntimeError)):
            tasks.update(record.id,task['id'],status='completed' if case=='wrong_terminal' else 'needs_review',result=body,hold=hold)
        assert tasks.get(record.id,task['id'])['status']=='queued'
    assert store.get(record.id,task['id']) is None and lessons.list(record)==before


def test_disabled_read_view_does_not_migrate_old_database(tmp_path):
    projects,*_=setup(tmp_path)
    view=HeldReviewStore(projects,create=False)
    assert view.get('project','task') is None
    with projects._connection() as c:
        assert c.execute("SELECT count(*) FROM sqlite_schema WHERE name='held_section_candidates'").fetchone()[0]==0


def test_hold_and_publish_cannot_invoke_a_callback_before_rejection(tmp_path):
    projects,record,tasks,*_=setup(tmp_path)
    task=tasks.create(record,'scripted',{})
    called=[]
    with pytest.raises(ValueError):
        tasks.update(record.id,task['id'],status='needs_review',hold=lambda _:called.append('hold'),publish=lambda _:called.append('publish'))
    assert called==[] and tasks.get(record.id,task['id'])['status']=='queued'
