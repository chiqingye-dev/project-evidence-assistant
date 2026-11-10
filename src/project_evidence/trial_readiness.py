"""Non-secret readiness/quota handshake for a future isolated trial runner.

The caller emits readiness only AFTER local getpass validation, then the agent
reads actual get_usage_limits and writes a nonce-bound receipt. No key argument,
key persistence, model dispatch or renewal of the original 300-second rule.
This data-only helper is not an execution entry and does not prove provenance
of an arbitrary caller-supplied dict; the runner must pin the trusted writer.
"""
from datetime import datetime, timezone
import re
from uuid import uuid4


class NotReady(ValueError):
    pass


def _utc(value):
    try:
        result=datetime.fromisoformat(value) if isinstance(value,str) else value
        if not isinstance(result,datetime) or result.utcoffset() is None: raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError,TypeError,OverflowError):
        raise NotReady('invalid_time') from None


def readiness(*, now):
    """Call after hidden local input; the signal itself contains no credentials."""
    return {'protocol':'key_ready_quota_v1','nonce':uuid4().hex,'ready_at':_utc(now).isoformat()}


def validate(ready, receipt, *, now):
    """Reject stale, pre-input, mismatched or insufficient quota; no side effects."""
    if (not isinstance(ready,dict) or set(ready)!={'protocol','nonce','ready_at'}
            or ready['protocol']!='key_ready_quota_v1'
            or not isinstance(ready['nonce'],str) or re.fullmatch('[0-9a-f]{32}',ready['nonce']) is None):
        raise NotReady('invalid_ready')
    allowed={'protocol','nonce','source','checked_at','ordinaryUsageAllowed','whole_unit_estimate_sufficient'}
    if not isinstance(receipt,dict) or set(receipt)!=allowed:
        raise NotReady('invalid_receipt')
    if (receipt['protocol']!=ready['protocol'] or receipt['nonce']!=ready['nonce']
            or receipt['source']!='mcp__codex_app__get_usage_limits'):
        raise NotReady('receipt_binding')
    if receipt['ordinaryUsageAllowed'] is not True or receipt['whole_unit_estimate_sufficient'] is not True:
        raise NotReady('quota_not_sufficient')
    ready_at,checked_at,current=(_utc(ready['ready_at']),_utc(receipt['checked_at']),_utc(now))
    if not ready_at<=checked_at<=current or not 0<=(current-checked_at).total_seconds()<=300:
        raise NotReady('quota_not_current')
    return True
