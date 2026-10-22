"""HTTP-клиент самостоятельного этапа. Ключей внешних AI API здесь нет."""
import asyncio
import base64
import aiohttp
from contracts.workflow import StageAnalysisResponse, StageImageResponse


class StageError(Exception):
    pass


class StageClient:
    def __init__(self, stage, url, token):
        self.stage, self.url, self.token = stage, url.rstrip('/'), token

    async def post(self, path, request, schema, timeout):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(self.url + path, json=request.model_dump(), headers={'Authorization': f'Bearer {self.token}'}) as response:
                    if response.status != 200:
                        if response.status == 502:
                            try:
                                body = await response.json()
                                code = body.get('error') if isinstance(body, dict) else None
                            except (ValueError, aiohttp.ClientError):
                                code = None
                            messages = {
                                'authentication': 'AI-сервис отклонил ключ доступа. Проверьте настройки ключа.',
                                'credits': 'AI-сервис сообщил о недостатке средств или кредитов для запроса.',
                                'rate_limit': 'AI-сервис временно ограничил частоту запросов. Повторите позже.',
                                'timeout': 'AI-сервис не ответил вовремя. Данные сохранены; можно повторить запрос.',
                                'incomplete': 'Ответ модели оборвался до завершения анализа. Данные сохранены; можно повторить запрос.',
                                'invalid_model_response': 'Модель вернула данные в неверном формате. Данные сохранены; можно повторить анализ.',
                                'invalid_geometry': 'Модель не смогла составить согласованную схему деталей. Ваши данные сохранены, но такой чертёж выдавать нельзя. Повторите анализ кнопкой «Попробовать ещё раз».',
                                'refusal': 'Модель отказалась обработать запрос. Данные сохранены.',
                            }
                            raise StageError(messages.get(code, 'AI-сервис не смог обработать запрос. Причина не уточнена; данные сохранены.'))
                        messages = {401: 'Проверьте внутренний ключ сервиса.', 409: 'Этот запрос уже обрабатывается.',
                                    410: 'Файл в кэше недоступен; повторите запрос.', 422: 'Сервис отклонил неполные или противоречивые данные этапа. Нужен повторный анализ.'}
                        raise StageError(messages.get(response.status, f'Сервис недоступен (HTTP {response.status}).'))
                    result = schema.model_validate(await response.json())
            if result.stage != self.stage:
                raise StageError('Получен результат другого этапа.')
            return result
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise StageError(f'Нет связи с сервисом {self.stage}. Запустите run_{self.stage}.py. Данные сохранены.') from exc
        except (ValueError, TypeError) as exc:
            raise StageError('Сервис вернул некорректный результат. Данные сохранены.') from exc

    async def analyse(self, request):
        return (await self.post('/v1/analyses', request, StageAnalysisResponse, 420 if self.stage == 'dimensions' else 210)).plan

    async def generate(self, request):
        result = await self.post('/v1/images', request, StageImageResponse, 660)
        if (result.project_id, result.revision) != (request.project_id, request.revision) or result.image.mime_type != 'image/png':
            raise StageError('Получено изображение другой заявки или неверного формата.')
        return base64.b64decode(result.image.data_base64), result.model
