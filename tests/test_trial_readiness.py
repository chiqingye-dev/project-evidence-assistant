"""Synthetic handshake checks, never fabricate a real quota receipt."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from project_evidence.trial_readiness import readiness, validate, NotReady

NOW=datetime(2026,11,10,8,tzinfo=timezone.utc)


def inputs():
    ready=readiness(now=NOW)
    receipt={'protocol':ready['protocol'],'nonce':ready['nonce'],'source':'mcp__codex_app__get_usage_limits',
        'checked_at':(NOW+timedelta(seconds=10)).isoformat(),'ordinaryUsageAllowed':True,'whole_unit_estimate_sufficient':True}
    return ready,receipt


def test_after_input_receipt_can_be_fresh_even_after_long_human_wait():
    ready,receipt=inputs(); receipt['checked_at']=(NOW+timedelta(hours=1)).isoformat()
    saved=deepcopy((ready,receipt))
    assert validate(ready,receipt,now=NOW+timedelta(hours=1,seconds=300))
    assert (ready,receipt)==saved
    assert set(ready)=={'protocol','nonce','ready_at'}
    assert readiness(now=NOW)['nonce']!=ready['nonce']


@pytest.mark.parametrize('case',[
    'pre_ready','expired','future','unknown','insufficient','bool_coercion','wrong_nonce','wrong_source',
    'wrong_protocol','missing','extra_secret','bad_time','naive_time','invalid_ready','copied_old_ready',
])
def test_stale_or_unbound_receipts_do_not_extend_300_seconds_or_authorize(case):
    ready,receipt=inputs(); current=NOW+timedelta(seconds=20)
    if case=='pre_ready':receipt['checked_at']=(NOW-timedelta(seconds=1)).isoformat()
    if case=='expired':current=NOW+timedelta(seconds=311)
    if case=='future':receipt['checked_at']=(NOW+timedelta(seconds=30)).isoformat()
    if case=='unknown':receipt['ordinaryUsageAllowed']=None
    if case=='insufficient':receipt['whole_unit_estimate_sufficient']=False
    if case=='bool_coercion':receipt['ordinaryUsageAllowed']=1
    if case=='wrong_nonce':receipt['nonce']='0'*32
    if case=='wrong_source':receipt['source']='manual'
    if case=='wrong_protocol':receipt['protocol']='old'
    if case=='missing':receipt.pop('checked_at')
    if case=='extra_secret':receipt['key']='synthetic-only'
    if case=='bad_time':receipt['checked_at']='invalid'
    if case=='naive_time':receipt['checked_at']='2026-11-10T08:00:10'
    if case=='invalid_ready':ready['nonce']='bad'
    if case=='copied_old_ready':ready=readiness(now=NOW)
    with pytest.raises(NotReady) as error:validate(ready,receipt,now=current)
    assert 'synthetic' not in str(error.value)
