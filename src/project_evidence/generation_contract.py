"""Public plan/body identity contract. No model, I/O, approval or release API.

Drafts are public authoring checklists, never private reasoning. Source rows
and exact body spans establish identity only. All prose needs independent review.
"""
from hashlib import sha256
from copy import deepcopy
from dataclasses import dataclass
import json
import re
from pydantic import ValidationError
from project_evidence.content_contract import _safe_text
from project_evidence.section_review import AcceptedReads, FrozenCandidate, encoded
from project_evidence.section_semantic_review import ReviewContract, fields
from project_evidence.source_coverage import BoundCoverage, projection, context_with_projection
from project_evidence.source_policy import normalize_github_repository, SourceError
from .generation_wire import CoverageDraft as Draft, BodyClaim


def digest(value):
    return sha256(encoded(value).encode()).hexdigest()


def bounded(value, limit=16000):
    if len(encoded(value).encode()) > limit:
        raise ValueError('coverage_contract_size')
    return value


_json = encoded


@dataclass(frozen=True)
class PublicBrief:
    """Host supplies public task objectives, independently of private answers."""
    objectives: tuple[str, ...]

    def __post_init__(self):
        if (type(self.objectives) is not tuple or not 1 <= len(self.objectives) <= 12 or
                any(type(x) is not str or not x.strip() or len(x) > 600 for x in self.objectives) or
                len(set(self.objectives)) != len(self.objectives)):
            raise ValueError('public_brief')
        for text in self.objectives:
            _safe_text(text)


_MINT = object()


class BoundPlan:
    __slots__ = ('_text', '_collector', '_mint')

    def __init__(self, value, collector, *, mint=None):
        if mint is not _MINT:
            raise ValueError('plan_authority')
        object.__setattr__(self, '_text', encoded(value))
        object.__setattr__(self, '_collector', collector)
        object.__setattr__(self, '_mint', mint)

    def __setattr__(self, *_):
        raise ValueError('plan_immutable')

    def view(self):
        return json.loads(self._text)


class PlannedFinal:
    __slots__ = ('_candidate', '_plan', '_mint')

    def __init__(self, candidate, plan, *, mint=None):
        if mint is not _MINT:
            raise ValueError('plan_final_authority')
        object.__setattr__(self, '_candidate', candidate)
        object.__setattr__(self, '_plan', plan)
        object.__setattr__(self, '_mint', mint)

    def __setattr__(self, *_):
        raise ValueError('plan_final_immutable')

    def view(self):
        return self._candidate.view()


def _contract(collector, contract):
    if type(collector) is not AcceptedReads or type(contract) is not ReviewContract:
        raise ValueError('plan_binding')
    if (contract.commit != collector.scope.commit or
            normalize_github_repository(contract.public_url) !=
            normalize_github_repository(collector.scope.public_url)):
        raise ValueError('plan_binding')


def planning_packet(bound, collector, evidence, contract, brief):
    """Require current opaque source authority; rows address the existing view."""
    _contract(collector, contract)
    if type(brief) is not PublicBrief:
        raise ValueError('public_brief')
    if type(bound) is not BoundCoverage:
        raise ValueError('plan_binding')
    view = projection(bound, collector, evidence)
    # Compact row IDs are scoped to BOTH digests, never accepted independently.
    catalog = {f'f:{i}': view['evidence_ids'][row[1]]
               for i, row in enumerate(view['facts'])}
    catalog.update({f'c:{i}': view['evidence_ids'][row[0]]
                    for i, row in enumerate(view['calls'])})
    if not catalog:
        raise ValueError('plan_empty_source')
    return bounded(dict(format='source_bound_planning_packet_v1',
        task_id=collector.scope.task_id, project_id=collector.scope.project_id,
        commit=collector.scope.commit, topic=collector.scope.topic,
        section_id=collector.scope.section_id, index_digest=view['source_digest'],
        coverage_digest=view['coverage_digest'], contract_digest=contract.digest,
        public_objectives=list(brief.objectives), brief_digest=digest(brief.objectives), targets=catalog,
        unsupported=view['unsupported'], omitted_by_host_selection=view['omitted_by_host_selection'],
        notice='Map every public authoring objective to current f:n and c:n rows or an explicit gap. '
               'Rows are syntax data, not mandatory facts or proof of explanation. '
               'Unknown receiver relationships stay unknown. No authority to publish.',
        semantic_verified=False, publication_approval=False))


def prepare(bound, collector, evidence, contract, brief, draft):
    """Mint while reads are open, before freeze; cannot retrofit a held answer."""
    packet = planning_packet(bound, collector, evidence, contract, brief)
    bounded(draft)
    try:
        checked = Draft.model_validate(draft)
    except (ValidationError, TypeError, ValueError):
        raise ValueError('plan_shape') from None
    if sorted(row.number for row in checked.criteria) != list(range(1, len(brief.objectives) + 1)):
        raise ValueError('plan_criteria')
    assigned = set()
    for row in checked.criteria:
        _safe_text(row.gap)
        if (len(set(row.targets)) != len(row.targets) or
                any(t not in packet['targets'] for t in row.targets) or
                (not row.targets and not row.gap.strip())):
            raise ValueError('plan_targets')
        assigned.update(row.targets)
    value = dict(packet, criteria=[r.model_dump() for r in checked.criteria],
        unplanned_targets=sorted(set(packet['targets']) - assigned),
        status='plan_bound_not_approved', generation_permission=False)
    value['plan_digest'] = digest(value)
    return BoundPlan(bounded(value, 32000), collector, mint=_MINT)


def planning_context(context, bound, collector, evidence, contract, brief, *, plan=None, max_chars=24000):
    """Offline frame construction only; count additions, never relax dispatch caps."""
    packet = planning_packet(bound, collector, evidence, contract, brief)
    value = deepcopy(context)
    history = value.get('history', [])
    if any('host_generation_coverage' in row for row in history):
        raise ValueError('duplicate_plan_context')
    old = [row for row in history if 'host_source_coverage' in row]
    if old:
        expected = collector.coverage_context(evidence, tuple(bound.view()['host_facets']))
        if len(old) != 1 or old[0] != expected:
            raise ValueError('foreign_coverage_context')
        value['history'] = [row for row in history if row not in old]
    # Reuse original excerpt/history/index verification. Its conservative JSON
    # measurement also remains in force; the final added data gets engine _json.
    value = context_with_projection(value, bound, collector, evidence)
    item = dict(public_objectives=packet['public_objectives'], brief_digest=packet['brief_digest'],
        contract_digest=contract.digest,
        coverage_digest=packet['coverage_digest'],
        instruction='Public authoring checklist only. Map each criterion to f:n and c:n '
            'rows in the existing combined view or an explicit source gap. '
            'Unplanned rows remain visible for independent review; do not infer '
            'unread relationships. No semantic or publication approval.')
    if plan is not None:
        p = _current(plan, collector, contract)
        if p['coverage_digest'] != packet['coverage_digest'] or p['brief_digest'] != packet['brief_digest']:
            raise ValueError('plan_coverage_changed')
        item.update(plan_digest=p['plan_digest'], criteria=p['criteria'],
                    unplanned_targets=p['unplanned_targets'])
    value['history'].append(dict(host_generation_coverage=item))
    if type(max_chars) is not int or not 1 <= max_chars <= 24000 or len(_json(value)) > max_chars:
        raise SourceError('budget_exhausted', '覆盖计划计入完整上下文后超过原上限。')
    return value


def _current(plan, collector, contract):
    _contract(collector, contract)
    if (type(plan) is not BoundPlan or plan._mint is not _MINT or
            plan._collector is not collector):
        raise ValueError('plan_authority')
    value = plan.view()
    if value['contract_digest'] != contract.digest:
        raise ValueError('plan_contract_changed')
    refs = [r for r, _ in collector._accepted.values()]
    if collector.index(refs).digest != value['index_digest']:
        raise ValueError('plan_source_changed')
    return value


def complete(plan, collector, contract, output, evidence):
    """Bind the original final exactly once using the existing hold mechanism."""
    _current(plan, collector, contract)
    collector._open()
    candidate = collector.freeze(output, evidence)
    return PlannedFinal(candidate, plan, mint=_MINT)


def final_report(plan, completed, collector, contract, claims):
    """Negative structural checks; never a semantic score or release permit."""
    p = _current(plan, collector, contract)
    if (type(completed) is not PlannedFinal or completed._mint is not _MINT or
            completed._plan is not plan or not collector._frozen):
        raise ValueError('plan_final_binding')
    candidate = completed._candidate
    if type(candidate) is not FrozenCandidate:
        raise ValueError('plan_final_binding')
    held = candidate.view()
    if (any(held[k] != p[k] for k in ('task_id', 'project_id', 'commit', 'topic', 'section_id', 'index_digest'))
            or held['output_digest'] != digest(held['output'])):
        raise ValueError('plan_final_binding')
    if type(claims) is not list or len(claims) > 256:
        raise ValueError('body_claim_shape')
    bounded(claims, 32000)
    available = fields(held['output'])
    expected = {(row['number'], t) for row in p['criteria'] for t in row['targets']}
    # Explicit source gaps also require a final unknown statement, not silence.
    expected.update((row['number'], None) for row in p['criteria'] if row['gap'].strip())
    seen = set(); bound_claims = []; findings = []
    for raw in claims:
        try:
            claim = BodyClaim.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            raise ValueError('body_claim_shape') from None
        key = (claim.number, claim.target)
        if key not in expected or key in seen:
            raise ValueError('body_claim_target')
        seen.add(key)
        _safe_text(claim.quote)
        if not claim.quote.strip():
            raise ValueError('body_claim_quote')
        text = available.get(claim.field)
        if text is None:
            raise ValueError('body_claim_field')
        start = text.find(claim.quote)
        if start < 0 or text.find(claim.quote, start + 1) >= 0:
            raise ValueError('body_claim_quote')
        row = dict(number=claim.number, target=claim.target, field=claim.field,
                   start=start, end=start + len(claim.quote), kind=claim.kind)
        bound_claims.append(row)
        def finding(reason):
            findings.append(dict(number=claim.number, target=claim.target, reason=reason))
        if claim.kind == 'relationship':
            finding('relationship_requires_independent_source_review')
        if claim.kind == 'unknown':
            finding('declared_unknown_not_explained')
        if claim.target is None and claim.kind != 'unknown':
            finding('source_gap_cannot_become_assertion')
        # Headings, title and gap lists never satisfy explanation-body presence.
        match = re.fullmatch(r'block:(\d+):text', claim.field)
        if claim.kind != 'unknown' and match is None:
            finding('explanation_not_in_body')
        if claim.target is not None and match:
            cited = held['output']['blocks'][int(match[1]) - 1]['evidence_ids']
            if p['targets'][claim.target] not in cited:
                finding('source_not_cited')
        # Narrow, negative-only detection. Arbitrary prose still needs a reviewer.
        without_refs = re.sub(r'\b[fc]:\d+\b', '', claim.quote)
        for eid in set(p['targets'].values()):
            without_refs = without_refs.replace(eid, '')
        if not re.sub(r'[\W_\d]+', '', without_refs, flags=re.UNICODE):
            finding('reference_list_only')
    missing = sorted(expected - seen, key=lambda x: (x[0], x[1] or ''))
    findings.extend(dict(number=n, target=t, reason='missing_body_claim') for n, t in missing)
    if held['audit']['claim_findings'] or held['audit']['bounded_body_audit']['findings']:
        findings.append(dict(reason='existing_call_audit_requires_review'))
    return bounded(dict(format='bound_generation_body_report_v1',
        task_id=p['task_id'], index_digest=p['index_digest'], coverage_digest=p['coverage_digest'],
        contract_digest=contract.digest, plan_digest=p['plan_digest'],
        output_digest=held['output_digest'], bound_claims=bound_claims,
        findings=findings, unplanned_targets=p['unplanned_targets'],
        structural_obligations_recorded=not findings,
        status='needs_independent_review', semantic_verified=False,
        publication_approval=False, publication_performed=False, output_modified=False), 64000)
