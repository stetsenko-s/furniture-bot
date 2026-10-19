"""Единственная связь бота с генератором — версионированный HTTP-контракт."""
import asyncio
import base64

import aiohttp
from pydantic import ValidationError

from contracts.models import SketchResponse


class SketchError(Exception):
    pass


class SketchClient:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    async def generate(self, request):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=660)) as session:
                async with session.post(self.url + "/v1/sketches",
                        json=request.model_dump(),
                        headers={"Authorization": f"Bearer {self.token}"}) as response:
                    if response.status != 200:
                        messages = {
                            401: "Проверьте SKETCH_SERVICE_TOKEN в настройках.",
                            409: "Эта версия уже обрабатывается. Повторите запрос позднее.",
                            422: "Генератор отклонил данные проекта.",
                            502: "OpenAI не создал изображение. Проверьте доступ к модели и баланс.",
                        }
                        raise SketchError(messages.get(response.status,
                            f"Генератор недоступен (HTTP {response.status})."))
                    result = SketchResponse.model_validate(await response.json())
            if result.project_id != request.project_id or result.revision != request.revision:
                raise SketchError("Генератор вернул результат другой версии.")
            if result.image.mime_type != "image/png":
                raise SketchError("Генератор вернул неожиданный формат изображения.")
            return base64.b64decode(result.image.data_base64), result.model
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise SketchError("Нет связи с генератором. Запустите run_sketch.py. "
                              "Проект сохранён; запрос можно повторить.") from exc
        except (ValidationError, ValueError) as exc:
            raise SketchError("Генератор вернул некорректный результат.") from exc
