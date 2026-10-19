"""Диалог и чтение эскиза. Генерация чертежей вынесена в HTTP-сервис."""
import asyncio
import json
from pathlib import Path
import aiohttp
from pydantic import ValidationError

from .models import DialogueResult

PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "analysis.md").read_text(encoding="utf-8")


class DialogueError(Exception):
    pass


def response_payload(model, brief, messages, references):
    content = [{"type": "input_text", "text": json.dumps({
        "current_brief": brief.model_dump(),
        "conversation": [{"role": m["role"], "text": m["text"]} for m in messages[-80:]],
    }, ensure_ascii=False)}]
    content.extend({"type": "input_image", "image_url": image.data_url(), "detail": "high"}
                   for image in references)
    return {
        "model": model, "instructions": PROMPT, "store": False,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": "furniture_brief",
                            "strict": True, "schema": DialogueResult.model_json_schema()}},
        "max_output_tokens": 3500,
    }


def openrouter_payload(model, brief, messages, references):
    original = response_payload(model, brief, messages, references)
    content = [{'type': 'text', 'text': original['input'][0]['content'][0]['text']}]
    content.extend({'type': 'image_url', 'image_url': {'url': image.data_url(), 'detail': 'high'}}
                   for image in references)
    return {'model': model, 'messages': [{'role': 'system', 'content': PROMPT},
                                        {'role': 'user', 'content': content}],
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'furniture_brief', 'strict': True, 'schema': DialogueResult.model_json_schema()}},
            'provider': {'require_parameters': True}, 'max_tokens': 3500, 'stream': False}


class DialogueClient:
    def __init__(self, api_key, model, endpoint=None, *, provider='openai'):
        if provider not in ('openai', 'openrouter'):
            raise ValueError('Неизвестный провайдер диалога.')
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.label = 'OpenRouter' if provider == 'openrouter' else 'OpenAI'
        self.endpoint = endpoint or ('https://openrouter.ai/api/v1/chat/completions'
                                     if provider == 'openrouter' else 'https://api.openai.com/v1/responses')

    async def analyse(self, brief, messages, references):
        payload = (openrouter_payload if self.provider == 'openrouter' else response_payload)(
            self.model, brief, messages, references)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
                async with session.post(self.endpoint, json=payload,
                        headers={"Authorization": f"Bearer {self.api_key}"}) as response:
                    if response.status != 200:
                        raise DialogueError(f"{self.label} вернул HTTP {response.status}. "
                                            "Проверьте ключ, доступ к модели и баланс.")
                    data = await response.json()
            if self.provider == 'openrouter':
                if data.get('error'):
                    raise DialogueError('OpenRouter не смог завершить запрос. Данные сохранены.')
                choice = data['choices'][0]
                message = choice['message']
                if message.get('refusal') or choice.get('finish_reason') == 'content_filter':
                    raise DialogueError('Модель отказалась обрабатывать этот запрос.')
                if choice.get('finish_reason') != 'stop':
                    raise DialogueError('Модель не завершила ответ. Повторите анализ.')
                if not isinstance(message.get('content'), str):
                    raise DialogueError('Модель вернула неполный ответ. Повторите анализ.')
                return DialogueResult.model_validate_json(message['content'])
            if data.get("status") != "completed":
                raise DialogueError("Модель не завершила ответ. Повторите анализ.")
            output = []
            for item in data.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") == "refusal":
                        raise DialogueError("Модель отказалась обрабатывать этот запрос.")
                    if part.get("type") == "output_text":
                        output.append(part["text"])
            return DialogueResult.model_validate_json("".join(output))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise DialogueError(f"Не удалось связаться с {self.label}. Данные сохранены; "
                                "можно повторить анализ.") from exc
        except (ValidationError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise DialogueError("Модель вернула неполный ответ. Данные сохранены; "
                                "повторите анализ.") from exc
