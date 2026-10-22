"""Declarative orthogonal geometry. Coordinates are expressions, never executable code.

X: left to right; Y: bottom to top; Z: front to back, in millimetres.
Empty expression means zero. Every nonzero coordinate comes from named dimensions.
"""
from typing import Literal
from pydantic import Field
from .models import Contract


class Term(Contract):
    dimension: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    coefficient: float = Field(ge=-100, le=100, multiple_of=0.25)


class Point(Contract):
    x: list[Term] = Field(max_length=40)
    y: list[Term] = Field(max_length=40)
    z: list[Term] = Field(max_length=40)


class Part(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    label: str = Field(min_length=1, max_length=120)
    kind: Literal['panel', 'divider', 'shelf', 'drawer_front', 'door', 'rod', 'back']
    low: Point
    high: Point


class Measure(Contract):
    dimension: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    view: Literal['front', 'side', 'top']
    axis: Literal['x', 'y', 'z']
    start: Point
    end: Point


class Drawing(Contract):
    format: Literal['orthogonal-v1']
    parts: list[Part] = Field(min_length=1, max_length=80)
    measures: list[Measure] = Field(min_length=3, max_length=120)
