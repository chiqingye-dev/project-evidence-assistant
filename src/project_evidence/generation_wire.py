"""Bounded public plan/claim data, never tool or review authority."""
from typing import Literal
from pydantic import Field
from .content_contract import StrictContract


class PlanRow(StrictContract):
    number: int = Field(ge=1, le=12)
    targets: list[str] = Field(max_length=256)
    gap: str = Field(max_length=600)


class CoverageDraft(StrictContract):
    criteria: list[PlanRow] = Field(min_length=1, max_length=12)


class BodyClaim(StrictContract):
    number: int = Field(ge=1, le=12)
    target: str | None
    field: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=1200)
    kind: Literal['explanation', 'unknown', 'relationship']
