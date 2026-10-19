"""HTTP-клиент анализа. Не знает моделей, промптов и правил мебели."""
import asyncio

import aiohttp
from pydantic import ValidationError

from contracts.models import AnalysisRequest, AnalysisResponse


class DialogueError(Exception):
    pass


class DialogueClient:
    def __init__(self, url, token):
        self.url = url.rstrip('/')
        self.token = token

    async def analyse(self, brief, messages, references):
        payload = AnalysisRequest(brief=brief, references=references,
            messages=[{'role': m['role'], 'text': m['text']} for m in messages])
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as session:
                async with session.post(self.url + '/v1/analyses', json=payload.model_dump(),
                        headers={'Authorization': f'Bearer {self.token}'}) as response:
                    if response.status != 200:
                        errors = {
                            401: 'Проверьте DIALOGUE_SERVICE_TOKEN в настройках.',
                            413: 'Заявка слишком велика для сервиса анализа.',
                            422: 'Сервис анализа отклонил формат заявки.',
                            502: 'Модель не завершила анализ. Проверьте ключ, доступ к модели и баланс сервиса dialogue.',
                        }
                        raise DialogueError(errors.get(response.status,
                            f'Сервис анализа недоступен (HTTP {response.status}).') +
                            ' Данные сохранены; можно повторить анализ.')
                    result = AnalysisResponse.model_validate(await response.json())
            return result.result
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise DialogueError('Нет связи с анализатором. Запустите run_dialogue.py. '
                                'Данные сохранены; можно повторить анализ.') from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise DialogueError('Сервис анализа вернул некорректный ответ. Данные сохранены.') from exc
