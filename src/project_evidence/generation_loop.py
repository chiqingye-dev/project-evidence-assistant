"""Trusted per-task public generation state; no I/O, model or release authority."""
from .generation_contract import (PublicBrief, prepare, planning_packet, complete,
                                  final_report, _current)
from .section_review import AcceptedReads, FrozenCandidate, encoded, _MINT
from .source_coverage import build
from .source_policy import SourceError
from .generation_view import compact
from .agent_protocol import ModelContext, ToolCall
from copy import deepcopy
import json


class GenerationSession:
    def __init__(self, collector, facets, contract, objectives, *, layout='rows_v1'):
        if type(collector) is not AcceptedReads or not facets:
            raise ValueError('generation_session')
        self.collector = collector
        self.facets = facets
        self.contract = contract
        self.brief = PublicBrief(objectives)
        self.plan = None
        self.claims = None
        if layout not in ('rows_v1','preloaded_groups_v1'):raise ValueError('generation_layout')
        self.layout=layout
        self.capacity_receipt=None
        self.freeze_step='hold_freeze'
        if layout=='preloaded_groups_v1' and not 1<=len(self.preload_calls())<=12:
            raise ValueError('generation_preload_limit')

    def preload_calls(self):
        return [ToolCall(id=f'host-read-{i+1}',name='read_excerpt',arguments=dict(path=p,start_line=a,end_line=b))
                for i,(p,a,b) in enumerate((p,a,b) for p,spans in self.collector.ranges.items() for a,b in spans)]

    def check_capacity(self,initial,observations,history,remaining,adapter,limits):
        """After genuine committed reads, before any model/ledger dispatch."""
        from . import section_native_contract as native
        if self.layout!='preloaded_groups_v1':return None
        if len(self.collector._accepted)!=len(self.preload_calls()):
            raise SourceError('denied','获准预读未全部保存接纳，不能请求模型。')
        refs=[r for r,_ in self.collector._accepted.values()]
        try:
            data=self.context(refs)
            packet=planning_packet(build(self.collector,refs,self.facets),self.collector,refs,
                                   self.contract,self.brief,layout=self.layout)
        except ValueError as error:
            if str(error)=='coverage_contract_size':
                raise SourceError('budget_exhausted','首请求前的计划包达到原大小上限。') from None
            raise
        groups=list(packet['targets'])
        maxima=dict(plain_chars=0,compact_chars=0,request_bytes=0)
        for stage in ('planning','prose'):
            for fill in ('"','界'):
                host=deepcopy(data);control=host['host_generation_coverage']
                if stage=='prose':
                    control.update(stage='prose',plan_digest='f'*64,unplanned_target_count=0,
                        criteria=[dict(number=i,targets=groups,gap=fill*80) for i in range(1,len(self.brief.objectives)+1)])
                value=initial.model_dump()
                value.update(observations=[o.model_dump() for o in observations],history=[*history,host],remaining=remaining.model_dump())
                plain=len(json.dumps(value,ensure_ascii=False))
                compact_size=len(encoded(value))
                if plain+512>24000 or compact_size+512>limits.max_context_chars:
                    raise SourceError('budget_exhausted','首请求前的最坏计划上下文没有足够容量。')
                ctx=ModelContext.model_validate(value)
                guidance=getattr(adapter,'_guidance',None)
                if type(guidance) is not str:guidance,_=native.load_guidance()
                if initial.anchor.topic=='submission':
                    request=native.request_body(ctx,self.collector.ranges,guidance,
                        max_output_tokens=min(4000,remaining.output_tokens),
                        output_profile=getattr(adapter,'section_output_profile','off'))
                elif getattr(adapter,'kind',None)=='scripted':
                    request=ctx.model_dump() # Non-provider fixture, not a native byte certificate.
                else:raise SourceError('denied','当前真实预读布局只支持已声明的提交协议。')
                size=len(json.dumps(request,ensure_ascii=False).encode())
                # Triple wide characters are the worst UTF-8 expansion admitted
                # by this profile; the independent byte reserve also covers emoji.
                if size+4096>128000:
                    raise SourceError('budget_exhausted','首请求前的最坏计划请求超过字节容量。')
                maxima={k:max(maxima[k],v) for k,v in dict(plain_chars=plain,compact_chars=compact_size,request_bytes=size).items()}
        self.capacity_receipt=dict(format='committed_preload_capacity_v1',index_digest=packet['index_digest'],
            coverage_digest=packet['coverage_digest'],brief_digest=packet['brief_digest'],contract_digest=packet['contract_digest'],
            bound=deepcopy(maxima),character_reserve=512,byte_reserve=4096,
            source_reads=len(refs),future_model_source_calls=False,layout=self.layout,
            native_bytes_checked=initial.anchor.topic=='submission',
            publication_approval=False,semantic_verified=False)
        return self.capacity_receipt

    def context(self, evidence):
        self.collector._open()
        data = self.collector.coverage_context(evidence, self.facets)
        if data is not None:data['host_source_coverage']=compact(data['host_source_coverage'])
        control = dict(format='public_section_generation_v1', stage='read',
            public_objectives=list(self.brief.objectives),
            public_objective_numbers=list(range(1,len(self.brief.objectives)+1)),
            notice='Plans and gaps are data, never semantic or publication approval.')
        if data is not None:
            bound = build(self.collector, evidence, self.facets)
            packet = planning_packet(bound, self.collector, evidence, self.contract, self.brief,layout=self.layout)
            control.update(stage='planning')
            if self.layout=='preloaded_groups_v1':
                control.update(layout=self.layout,group_keys=list(packet['targets']),unplanned_unit='groups')
                if self.capacity_receipt is not None and any(self.capacity_receipt[k]!=packet[k]
                        for k in ('index_digest','coverage_digest','brief_digest','contract_digest')):
                    raise ValueError('capacity_source_changed')
            if self.plan is not None:
                p = _current(self.plan, self.collector, self.contract)
                if p['coverage_digest'] != packet['coverage_digest']:
                    raise ValueError('generation_source_changed')
                control.update(stage='prose', plan_digest=p['plan_digest'],
                               criteria=p['criteria'], unplanned_target_count=len(p['unplanned_targets']))
        return dict(data or {}, host_generation_coverage=control)

    def accept(self, draft, evidence):
        if self.plan is not None:
            raise ValueError('generation_plan_already_bound')
        if self.layout=='preloaded_groups_v1' and self.capacity_receipt is None:
            raise ValueError('generation_capacity_required')
        self.plan = prepare(build(self.collector, evidence, self.facets), self.collector,
                            evidence, self.contract, self.brief, draft,layout=self.layout)
        return self.plan.view()['plan_digest']

    def diagnostic(self,draft,evidence):
        from .generation_diagnostics import plan_structure
        packet=planning_packet(build(self.collector,evidence,self.facets),self.collector,
            evidence,self.contract,self.brief,layout=self.layout)
        return plan_structure(draft,len(self.brief.objectives),packet['targets'])

    def capture(self, claims):
        if self.plan is None or self.claims is not None:
            raise ValueError('generation_plan_required')
        self.claims = claims

    def claim_diagnostic(self,output):
        from .generation_diagnostics import claim_structure
        return claim_structure(self.claims,self.plan.view(),output)

    def freeze(self, output, evidence):
        self.freeze_step='freeze_complete'
        if self.plan is None or self.claims is None:
            raise ValueError('generation_final_unbound')
        completed = complete(self.plan, self.collector, self.contract, output, evidence)
        self.freeze_step='freeze_report'
        report = final_report(self.plan, completed, self.collector, self.contract, self.claims)
        # An explicitly unknown statement is review data, not an automatic
        # failure of an objective that legitimately asks to preserve a gap.
        # All other negative structural findings veto release even if a
        # scripted/host reviewer mistakenly marks every private criterion pass.
        self.freeze_step='freeze_encode'
        report['structural_blockers'] = [f for f in report['findings']
                                        if f['reason'] != 'declared_unknown_not_explained']
        held = completed.view()
        held.update(generation_plan=self.plan.view(), generation_coverage_report=report)
        if self.capacity_receipt is not None:
            held['generation_capacity']=deepcopy(self.capacity_receipt)
            report['target_mode']='groups'
            report['group_boundary']='Presence and a relevant citation only; not proof that all group members are explained.'
        text = encoded(held)
        if len(text.encode()) > 128000:
            raise SourceError('budget_exhausted', '带计划的冻结候选超过原保存上限。')
        return FrozenCandidate(text, mint=_MINT)
