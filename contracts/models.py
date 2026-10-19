"""Версионированный формат обмена. Без Telegram, БД и бизнес-логики."""
import base64
import binascii
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 4


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Brief(Contract):
    title: str = Field(max_length=100)
    description: str = Field(max_length=6000)
    width_mm: float | None = Field(gt=0)
    height_mm: float | None = Field(gt=0)
    depth_mm: float | None = Field(gt=0)
    material: str | None = Field(max_length=500)
    color: str | None = Field(max_length=200)
    hardware: str | None = Field(max_length=1000)

    @classmethod
    def empty(cls):
        return cls(title="Новый проект", description="", width_mm=None,
                   height_mm=None, depth_mm=None, material=None, color=None, hardware=None)


class ReferenceImage(Contract):
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_base64: str = Field(min_length=4, max_length=14 * 1024 * 1024)

    @field_validator("data_base64")
    @classmethod
    def validate_image(cls, value):
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Некорректный base64") from exc
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Изображение должно быть не больше 10 МБ")
        return value

    def data_url(self):
        return f"data:{self.mime_type};base64,{self.data_base64}"

    @field_validator("data_base64")
    @classmethod
    def validate_signature(cls, value, info):
        data = base64.b64decode(value)
        mime = info.data.get("mime_type")
        valid = {
            "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": data.startswith(b"\xff\xd8\xff"),
            "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        }
        if mime in valid and not valid[mime]:
            raise ValueError("Содержимое файла не соответствует формату изображения")
        return value


class SketchRequest(Contract):
    contract_version: Literal[1] = 1
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    project_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    brief: Brief
    references: list[ReferenceImage] = Field(min_length=1, max_length=MAX_IMAGES)


class SketchResponse(Contract):
    contract_version: Literal[1] = 1
    project_id: str
    revision: int
    image: ReferenceImage
    model: str


class ConversationMessage(Contract):
    role: Literal["user", "assistant"]
    text: str


class AnalysisRequest(Contract):
    contract_version: Literal[1] = 1
    brief: Brief
    messages: list[ConversationMessage]
    references: list[ReferenceImage] = Field(max_length=MAX_IMAGES)


class AnalysisResult(Contract):
    brief: Brief
    reply: str = Field(min_length=1, max_length=3000)
    questions: list[str]


class AnalysisResponse(Contract):
    contract_version: Literal[1] = 1
    result: AnalysisResult
