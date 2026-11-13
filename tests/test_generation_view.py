from copy import deepcopy
import pytest
from project_evidence.generation_view import compact, expand
from project_evidence.source_coverage import build, projection, FAMILIES
from test_source_coverage_binding import collected


def view():
    c,refs,_=collected()
    return projection(build(c,refs,tuple(FAMILIES)),c,refs)


def test_lossless_transport_retains_all_facts_calls_groups_gaps_and_digests():
    raw=view();raw['calls'] += [[0,2,4,'longReceiver.accept','explicit_callable'],[0,4,4,'longReceiver.other','unknown'],[0,5,5,'longReceiver.finish','unknown']]
    raw['unsupported']=[dict(evidence_id=raw['evidence_ids'][0],reason='unknown_form')]*3
    before=deepcopy(raw);packed=compact(raw)
    assert raw==before and expand(packed)==before
    assert packed['calls'][-1][2][0]==0
    assert all(not packed[k] for k in ('semantic_verified','publication_approval'))


@pytest.mark.parametrize('case',['negative_code','bool_code','bad_span','reversed_span','bad_receiver','wrong_format'])
def test_invalid_transport_never_recovers_a_bound_authority(case):
    packed=compact(view())
    if case=='negative_code':packed['calls'][0].append(-1)
    if case=='bool_code':packed['calls'][0].append(True)
    if case=='bad_span':packed['facts'][0][2]=True
    if case=='reversed_span':packed['facts'][0][2]=[4,2]
    if case=='bad_receiver':packed['calls'][0][2]=[-1,'madeUp']
    if case=='wrong_format':packed['format']='unknown'
    with pytest.raises(ValueError):expand(packed)


def test_passing_default_is_syntax_only_not_absence_or_receiver_proof():
    packed=compact(view());raw=expand(packed)
    assert raw['calls'] and all(r[-1]=='unknown' for r in raw['calls'])
    assert not raw['semantic_verified'] and not raw['publication_approval']
