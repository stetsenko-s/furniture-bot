"""API независимых этапов размеров и рендеринга, версия 1."""
from typing import Literal
from pydantic import Field
from .models import Contract, Brief, ConversationMessage, ReferenceImage, SketchResponse
from .drawing import Drawing, Term
from .cabinet import Cabinet

Stage = Literal['dimensions', 'render']


class Dimension(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    target: str = Field(min_length=1, max_length=200)
    axis: Literal['width', 'height', 'depth', 'gap', 'offset', 'thickness']
    value_mm: float | None = Field(gt=0)
    source_quote: str | None = Field(max_length=1500)
    formula: list[Term] | None = Field(default=None, min_length=1, max_length=12)


class StagePlan(Contract):
    brief: Brief
    dimensions: list[Dimension] = Field(max_length=120)
    inventory_complete: bool
    needs_sketch_changes: bool
    questions: list[str] = Field(max_length=150)
    reply: str = Field(min_length=1, max_length=3000)
    drawing: Drawing | None = None
    drawing_locked: bool = False
    cabinet: Cabinet | None = None


class StageAnalysisRequest(Contract):
    contract_version: Literal[1] = 1
    stage: Stage
    brief: Brief
    plan: StagePlan | None
    messages: list[ConversationMessage]
    references: list[ReferenceImage] = Field(min_length=1, max_length=4)


class StageAnalysisResponse(Contract):
    contract_version: Literal[1] = 1
    stage: Stage
    plan: StagePlan


class StageImageRequest(Contract):
    contract_version: Literal[1] = 1
    stage: Stage
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    project_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(ge=1)
    plan: StagePlan
    references: list[ReferenceImage] = Field(min_length=1, max_length=4)


class StageImageResponse(SketchResponse):
    stage: Stage
