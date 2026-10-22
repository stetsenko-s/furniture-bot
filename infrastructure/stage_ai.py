"""Транспорт AI API. Не содержит правил мебели, состояния проекта или Telegram."""
import asyncio
import json
import aiohttp
from contracts.models import ReferenceImage


class AIError(Exception):
    def __init__(self, message, code='provider_failed'):
        super().__init__(message)
        self.code = code if code in {'provider_failed', 'authentication', 'credits', 'rate_limit', 'timeout', 'incomplete', 'invalid_model_response', 'invalid_geometry', 'refusal'} else 'provider_failed'


def strict_schema(model):
    """API strict mode requires even nullable compatibility fields to be required."""
    schema = model.model_json_schema()
    def visit(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object':
                node['required'] = list(node.get('properties', {}))
                node['additionalProperties'] = False
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(schema)
    return schema


class StageAI:
    def __init__(self, key, text_model, image_model, *, provider='openai', quality='medium', size='1536x1024', text_endpoint=None, image_endpoint=None):
        if provider not in ('openai', 'openrouter'):
            raise ValueError('Неизвестный провайдер')
        self.key, self.text_model, self.image_model = key, text_model, image_model
        self.provider, self.quality, self.size = provider, quality, size
        self.text_endpoint = text_endpoint or ('https://openrouter.ai/api/v1/chat/completions' if provider == 'openrouter' else 'https://api.openai.com/v1/responses')
        self.image_endpoint = image_endpoint or ('https://openrouter.ai/api/v1/images' if provider == 'openrouter' else 'https://api.openai.com/v1/images/edits')

    async def post(self, endpoint, payload, timeout):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(endpoint, json=payload, headers={'Authorization': f'Bearer {self.key}'}) as response:
                    if response.status != 200:
                        code = {401: 'authentication', 402: 'credits', 403: 'authentication', 429: 'rate_limit'}.get(response.status, 'provider_failed')
                        raise AIError(f'Ошибка провайдера HTTP {response.status}', code)
                    return await response.json()
        except asyncio.TimeoutError as exc:
            raise AIError('Превышено время ожидания провайдера', 'timeout') from exc
        except (aiohttp.ClientError, ValueError) as exc:
            raise AIError('Нет корректного ответа провайдера') from exc

    async def analyse(self, instructions, data, references, schema):
        context = json.dumps(data, ensure_ascii=False)
        output_limit = getattr(schema, 'OUTPUT_LIMIT', 10000)
        if self.provider == 'openrouter':
            content = [{'type': 'text', 'text': context}] + [
                {'type': 'image_url', 'image_url': {'url': r.data_url(), 'detail': 'high'}} for r in references]
            payload = {'model': self.text_model, 'messages': [{'role': 'system', 'content': instructions}, {'role': 'user', 'content': content}],
                       'response_format': {'type': 'json_schema', 'json_schema': {'name': 'stage_plan', 'strict': True, 'schema': strict_schema(schema)}},
                       'provider': {'require_parameters': True}, 'max_tokens': output_limit, 'stream': False}
        else:
            content = [{'type': 'input_text', 'text': context}] + [
                {'type': 'input_image', 'image_url': r.data_url(), 'detail': 'high'} for r in references]
            payload = {'model': self.text_model, 'instructions': instructions, 'store': False,
                       'input': [{'role': 'user', 'content': content}], 'max_output_tokens': output_limit,
                       'text': {'format': {'type': 'json_schema', 'name': 'stage_plan', 'strict': True, 'schema': strict_schema(schema)}}}
        result = await self.post(self.text_endpoint, payload, 180)
        try:
            if self.provider == 'openrouter':
                choice = result['choices'][0]
                if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
                    raise AIError('Модель не завершила анализ', 'refusal' if choice['message'].get('refusal') else 'incomplete')
                text = choice['message']['content']
            else:
                if result.get('status') != 'completed':
                    raise AIError('Модель не завершила анализ', 'incomplete')
                parts = [part for item in result.get('output', []) for part in item.get('content', [])]
                if any(part.get('type') == 'refusal' for part in parts):
                    raise AIError('Модель отказалась от анализа', 'refusal')
                text = ''.join(part['text'] for part in parts if part.get('type') == 'output_text')
            return schema.model_validate_json(text)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise AIError('Некорректный ответ модели', 'invalid_model_response') from exc

    async def image(self, instructions, plan, references):
        payload = {'model': self.image_model, 'prompt': instructions + '\n\n' + plan.model_dump_json(),
                   'quality': self.quality, 'size': self.size, 'output_format': 'png', 'n': 1}
        if self.provider == 'openrouter':
            payload['input_references'] = [{'type': 'image_url', 'image_url': {'url': r.data_url()}} for r in references]
        else:
            payload['images'] = [{'image_url': r.data_url()} for r in references]
        data = await self.post(self.image_endpoint, payload, 600)
        try:
            entry = data['data'][0]
            if entry.get('media_type', 'image/png') != 'image/png':
                raise AIError('Ожидалось изображение PNG')
            return ReferenceImage(mime_type='image/png', data_base64=entry['b64_json']), self.image_model
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AIError('Некорректное изображение') from exc
