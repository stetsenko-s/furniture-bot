"""Внутренний ответ агента; служебные исправления не меняют HTTP-контракт."""
from pydantic import Field
from typing import ClassVar, Literal
from contracts.models import Contract, Brief
from contracts.workflow import StagePlan
from contracts.cabinet import Cabinet


class RemovedDimension(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    source_quote: str = Field(min_length=1, max_length=1500)


class DimensionAlias(Contract):
    """Two names for the same user measurement, never a new numeric value."""
    duplicate_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    canonical_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    source_quote: str = Field(min_length=1, max_length=1500)


class ShelfGapLink(Contract):
    section_id: str
    dimension_id: str
    shelf_ids: list[str] = Field(min_length=2, max_length=30)
    basis: Literal['clear_gap', 'pitch'] | None
    source_quote: str = Field(min_length=1, max_length=1500)


class DimensionLinkRepair(Contract):
    OUTPUT_LIMIT: ClassVar[int] = 4000
    shelf_gaps: list[ShelfGapLink] = Field(max_length=30)
    dimension_aliases: list[DimensionAlias] = Field(max_length=120)
    questions: list[str] = Field(max_length=20)


class DimensionsAnalysis(StagePlan):
    OUTPUT_LIMIT: ClassVar[int] = 20000
    removed_dimensions: list[RemovedDimension] = Field(max_length=120)
    dimension_aliases: list[DimensionAlias] = Field(default_factory=list, max_length=120)


class SourceDimension(Contract):
    """The model supplies measurements; the compiler owns derived formulae."""
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    target: str = Field(min_length=1, max_length=200)
    axis: Literal['width', 'height', 'depth', 'gap', 'offset', 'thickness']
    value_mm: float | None = Field(gt=0)
    source_quote: str | None = Field(max_length=1500)


class CabinetAnalysis(Contract):
    """Compact wire schema: no generated coordinates or model-owned locks."""
    OUTPUT_LIMIT: ClassVar[int] = 10000
    removed_dimensions: list[RemovedDimension] = Field(max_length=120)
    dimension_aliases: list[DimensionAlias] = Field(default_factory=list, max_length=120)
    brief: Brief
    dimensions: list[SourceDimension] = Field(max_length=120)
    inventory_complete: bool
    needs_sketch_changes: bool
    questions: list[str] = Field(max_length=150)
    reply: str = Field(min_length=1, max_length=3000)
    cabinet: Cabinet | None
