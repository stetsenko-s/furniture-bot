"""Внутренний ответ агента; служебные исправления не меняют HTTP-контракт."""
from pydantic import Field
from contracts.models import Contract
from contracts.workflow import StagePlan


class RemovedDimension(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    source_quote: str = Field(min_length=1, max_length=1500)


class DimensionsAnalysis(StagePlan):
    removed_dimensions: list[RemovedDimension] = Field(max_length=120)
