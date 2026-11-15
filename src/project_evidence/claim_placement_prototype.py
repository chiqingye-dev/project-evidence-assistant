"""Offline-only carrier experiment: deterministic IDs/locations, unchanged audit.

Not imported by the live adapter. Model-authored placements explicitly choose a
public obligation and kind; the host derives its pair and the containing field.
This never fills missing obligations, changes prose, freezes or publishes.
"""
from .generation_contract import BoundPlan
from .generation_wire import BodyClaim

def catalog(plan):
    if type(plan) is not BoundPlan:raise ValueError('active_bound_plan_required')
    value=plan.view()
    pairs={(row['number'],target) for row in value['criteria'] for target in row['targets']}
    pairs.update((row['number'],None) for row in value['criteria'] if row['gap'].strip())
    return [dict(id=f'p{i}',number=n,target=t) for i,(n,t) in enumerate(sorted(pairs,key=lambda x:(x[0],x[1] or '')),1)]

def derive(plan,output,block_placements,gap_placements):
    """Pure original-claim construction; caller must still use final_report/review."""
    choices={row['id']:row for row in catalog(plan)}
    if (type(output) is not dict or type(block_placements) is not list or type(gap_placements) is not list
            or len(block_placements)!=len(output['blocks']) or len(gap_placements)!=len(output['gaps'])):
        raise ValueError('placement_shape')
    seen=set();claims=[]
    for prefix,locations,placements in (('block',output['blocks'],block_placements),('gap',output['gaps'],gap_placements)):
        for index,(location,rows) in enumerate(zip(locations,placements),1):
            if type(rows) is not list or len(rows)>256:raise ValueError('placement_shape')
            for row in rows:
                if type(row) is not dict or set(row)!={'id','kind'} or type(row['id']) is not str or row['id'] not in choices:
                    raise ValueError('placement_choice')
                if row['id'] in seen:raise ValueError('placement_duplicate')
                pair=choices[row['id']]
                claim=BodyClaim.model_validate(dict(number=pair['number'],target=pair['target'],
                    field=f'block:{index}:text' if prefix=='block' else f'gap:{index}',
                    quote=location['text'] if prefix=='block' else location,kind=row['kind']))
                seen.add(row['id']);claims.append(claim.model_dump())
    return claims
