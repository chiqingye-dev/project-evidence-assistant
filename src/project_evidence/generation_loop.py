"""Trusted per-task public generation state; no I/O, model or release authority."""
from .generation_contract import (PublicBrief, prepare, planning_packet, complete,
                                  final_report, _current)
from .section_review import AcceptedReads, FrozenCandidate, encoded, _MINT
from .source_coverage import build
from .source_policy import SourceError


class GenerationSession:
    def __init__(self, collector, facets, contract, objectives):
        if type(collector) is not AcceptedReads or not facets:
            raise ValueError('generation_session')
        self.collector = collector
        self.facets = facets
        self.contract = contract
        self.brief = PublicBrief(objectives)
        self.plan = None
        self.claims = None

    def context(self, evidence):
        self.collector._open()
        data = self.collector.coverage_context(evidence, self.facets)
        control = dict(format='public_section_generation_v1', stage='read',
            public_objectives=list(self.brief.objectives),
            notice='Public authoring checklist only; f:n and c:n rows refer to the current '
                   'combined source view. Unknown source remains a gap. '
                   'A plan or a filled table is not semantic or publication approval.')
        if data is not None:
            bound = build(self.collector, evidence, self.facets)
            packet = planning_packet(bound, self.collector, evidence, self.contract, self.brief)
            control.update(stage='planning', coverage_digest=packet['coverage_digest'],
                           index_digest=packet['index_digest'], brief_digest=packet['brief_digest'])
            if self.plan is not None:
                p = _current(self.plan, self.collector, self.contract)
                if p['coverage_digest'] != packet['coverage_digest']:
                    raise ValueError('generation_source_changed')
                control.update(stage='prose', plan_digest=p['plan_digest'],
                               criteria=p['criteria'], unplanned_targets=p['unplanned_targets'])
        return dict(data or {}, host_generation_coverage=control)

    def accept(self, draft, evidence):
        if self.plan is not None:
            raise ValueError('generation_plan_already_bound')
        self.plan = prepare(build(self.collector, evidence, self.facets), self.collector,
                            evidence, self.contract, self.brief, draft)
        return self.plan.view()['plan_digest']

    def capture(self, claims):
        if self.plan is None or self.claims is not None:
            raise ValueError('generation_plan_required')
        self.claims = claims

    def freeze(self, output, evidence):
        if self.plan is None or self.claims is None:
            raise ValueError('generation_final_unbound')
        completed = complete(self.plan, self.collector, self.contract, output, evidence)
        report = final_report(self.plan, completed, self.collector, self.contract, self.claims)
        # An explicitly unknown statement is review data, not an automatic
        # failure of an objective that legitimately asks to preserve a gap.
        # All other negative structural findings veto release even if a
        # scripted/host reviewer mistakenly marks every private criterion pass.
        report['structural_blockers'] = [f for f in report['findings']
                                        if f['reason'] != 'declared_unknown_not_explained']
        held = completed.view()
        held.update(generation_plan=self.plan.view(), generation_coverage_report=report)
        text = encoded(held)
        if len(text.encode()) > 128000:
            raise SourceError('budget_exhausted', '带计划的冻结候选超过原保存上限。')
        return FrozenCandidate(text, mint=_MINT)
