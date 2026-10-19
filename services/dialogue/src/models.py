"""Внутренняя схема ответа модели анализа, не API бота."""
from typing import Literal
from pydantic import Field
from contracts.models import Contract, Brief

class CabinetSection(Contract):
    """Проверка секции при анализе; не меняет контракт сервиса изображений."""
    name: str = Field(min_length=1, max_length=150)
    doors: Literal["unknown", "present", "absent"]
    no_doors_user_quote: str | None = Field(max_length=1000)
    door_details_known: bool
    interior_known: bool


class Clarification(Contract):
    """Тема и отделение позволяют объединить вопрос с программной проверкой."""
    text: str = Field(min_length=1, max_length=1000)
    topic: Literal["doors", "interior", "contradiction", "other"]
    section_index: int | None = Field(ge=0)


class DialogueResult(Contract):
    brief: Brief
    reply: str = Field(min_length=1, max_length=3000)
    questions: list[Clarification] = Field(max_length=6)
    is_cabinet: bool
    cabinet_sections: list[CabinetSection] = Field(max_length=30)
