"""Semantic cabinet construction: the model names dimensions, not coordinates."""
from typing import Literal
from pydantic import Field
from .models import Contract


class CabinetElement(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,45}$')
    label: str = Field(min_length=1, max_length=120)
    kind: Literal['shelf', 'drawer_front', 'rod']
    width_mode: Literal['opening', 'dimension'] | None
    width_dimension: str | None
    alignment: Literal['left', 'center', 'right'] | None
    position_reference: Literal['floor', 'top', 'inner_bottom', 'previous_top'] | None
    offset_dimension: str | None
    height_dimension: str | None
    depth_dimension: str | None


class CabinetDoor(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    label: str = Field(min_length=1, max_length=120)
    width_dimension: str | None
    height_dimension: str | None
    bottom_offset_dimension: str | None


class CabinetSection(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    label: str = Field(min_length=1, max_length=120)
    width_dimension: str | None
    elements: list[CabinetElement] = Field(max_length=30)
    door: CabinetDoor | None


class Cabinet(Contract):
    panel_thickness_dimension: str | None
    facade_thickness_dimension: str | None
    width_basis: Literal['partition_centers', 'clear_openings'] | None
    joint: Literal['between_sides', 'over_sides'] | None
    sections: list[CabinetSection] = Field(min_length=1, max_length=12)
