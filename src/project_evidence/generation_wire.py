"""Bounded public plan/claim data, never tool or review authority."""
from typing import Literal
from functools import lru_cache
from pydantic import Field,create_model
from .content_contract import StrictContract


class PlanRow(StrictContract):
    number: int = Field(ge=1, le=12)
    targets: list[str] = Field(max_length=256)
    gap: str = Field(max_length=600)


class CoverageDraft(StrictContract):
    criteria: list[PlanRow] = Field(min_length=1, max_length=12)


class GroupRow(StrictContract):
    number: int = Field(ge=1, le=12)
    targets: list[str] = Field(max_length=8)
    gap: str = Field(max_length=80)


class GroupDraft(StrictContract):
    criteria: list[GroupRow] = Field(min_length=1, max_length=12)


@lru_cache(maxsize=24)
def objective_plan_model(group,count):
    """The provider schema follows the same public objective count as the host.

    The original host still verifies numbering/uniqueness and source identity.
    This model is for request schema construction, not an alternate release path.
    """
    if type(group) is not bool or type(count) is not int or not 1<=count<=12:
        raise ValueError('public_objective_count')
    row=create_model(f'PublicObjectiveRow{int(group)}N{count}',__base__=GroupRow if group else PlanRow,
        number=(int,Field(ge=1,le=count)))
    return create_model(f'PublicObjectivePlan{int(group)}N{count}',__base__=GroupDraft if group else CoverageDraft,
        criteria=(list[row],Field(min_length=count,max_length=count)))


class BodyClaim(StrictContract):
    number: int = Field(ge=1, le=12)
    target: str | None
    field: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=1200)
    kind: Literal['explanation', 'unknown', 'relationship']
