"""Host call syntax enters the measured context; never a new permission."""
from copy import deepcopy
import json
import pytest
from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import ModelContext,AgentBudget,ScriptedAdapter,SECTION_GOAL
from project_evidence.section_native_contract import build_request
from project_evidence.source_policy import SourceError
from test_section_review import collected
from test_call_binding import fixture
from test_agent_engine import CONTEXT,gap_turn
from test_learning_engine import anchor


def context():
    chosen=anchor(True);chosen['topic']='submission'
    return ModelContext(**CONTEXT,goal=SECTION_GOAL,anchor=chosen)


def test_current_accepted_call_rows_reach_native_request_without_changing_source_ranges():
    collector,refs=collected();before=deepcopy(refs)
    data=collector.model_context(refs)
    value=context().model_dump();value['history']=[data]
    request=build_request(ModelContext.model_validate(value),{'src/Example.java':((4,4),)})
    payload=json.loads(request['messages'][1]['content'])
    view=payload['history'][0]['host_source_call_evidence']
    assert {c[2] for c in view['calls']}=={'send','work'}
    assert view['evidence_ids']==['evidence-one'] and view['publication_approval'] is False
    assert 'outside' not in json.dumps(view) and refs==before
    assert request['max_tokens']==4000


def test_unaccepted_read_or_forged_final_references_do_not_appear_in_projection():
    scope,*_=fixture()
    from project_evidence.section_review import AcceptedReads
    c=AcceptedReads(scope)
    assert c.model_context([]) is None
    collector,refs=collected();refs[0]['evidence_id']='unread'
    with pytest.raises(SourceError):collector.model_context(refs)


def test_enrichment_is_budgeted_before_model_dispatch_and_never_truncated():
    adapter=ScriptedAdapter([lambda _:pytest.fail('over-budget call')])
    result=run_agent(adapter,lambda *_:pytest.fail('tool'),lambda:None,lambda:False,
        AgentBudget(max_context_chars=1000),initial_context=context(),host_context=lambda _:dict(extra='x'*6000))
    assert result.status=='budget_exhausted' and adapter.calls==result.counters.model_calls==0


@pytest.mark.parametrize('case',['cancel','source','foreign_goal'])
def test_source_and_cancellation_precedence_survives_projection(case):
    state=dict(cancel=False,stale=False)
    def guard():
        if state['stale']:raise SourceError('unverified','synthetic session change')
    def host(_):
        state['cancel']=case=='cancel';state['stale']=case=='source';return dict(data='public')
    adapter=ScriptedAdapter([gap_turn()])
    result=run_agent(adapter,lambda *_:{},guard,lambda:state['cancel'],initial_context=CONTEXT if case=='foreign_goal' else context(),host_context=host)
    assert result.status==dict(cancel='cancelled',source='unverified',foreign_goal='failed')[case]
    assert adapter.calls==result.counters.model_calls==0


def test_legacy_context_is_unchanged_when_no_host_projection_is_enabled():
    def finish(value):
        assert all('host_source_call_evidence' not in h for h in value.history)
        return gap_turn()
    result=run_agent(ScriptedAdapter([finish]),lambda *_:{},lambda:None,lambda:False,initial_context=context())
    assert result.status=='gap'
