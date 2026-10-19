"""Одна задача: получить черно-белый эскиз из описания и референсов."""
import asyncio
import json
from pathlib import Path

import aiohttp

from contracts.models import ReferenceImage


PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "sketch.md").read_text(encoding="utf-8")


class ProviderError(Exception):
    pass


def image_payload(request, model, quality, size):
    prompt = PROMPT + json.dumps(request.brief.model_dump(), ensure_ascii=False)

    return {
        "model": model,
        "prompt": prompt,
        "images": [
            {"image_url": image.data_url()}
            for image in request.references
        ],
        "quality": quality,
        "size": size,
        "output_format": "png",
        "n": 1,
    }


class ImageProvider:
    def __init__(self, key, model, quality, size,
                 endpoint=None, *, provider='openai'):
        if provider not in ('openai', 'openrouter'):
            raise ValueError('Неизвестный провайдер изображений.')
        self.key, self.model = key, model
        self.provider = provider
        self.label = 'OpenRouter' if provider == 'openrouter' else 'OpenAI'
        self.quality, self.size = quality, size
        self.endpoint = endpoint or ('https://openrouter.ai/api/v1/images'
                                     if provider == 'openrouter' else 'https://api.openai.com/v1/images/edits')

    async def generate(self, request):
        payload = image_payload(request, self.model, self.quality, self.size)
        if self.provider == 'openrouter':
            payload.pop('images')
            payload['input_references'] = [{'type': 'image_url', 'image_url': {'url': image.data_url()}}
                                           for image in request.references]
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
                async with session.post(self.endpoint,
                        json=payload,
                        headers={"Authorization": f"Bearer {self.key}"}) as response:
                    if response.status != 200:
                        raise ProviderError(f"{self.label} HTTP {response.status}")
                    data = await response.json()
            entry = data['data'][0]
            if entry.get('media_type', 'image/png') != 'image/png':
                raise ProviderError('Модель вернула изображение в неподдерживаемом формате; нужен PNG.')
            image = ReferenceImage(mime_type="image/png", data_base64=entry['b64_json'])
            return image, self.model
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError(f"Не удалось получить изображение от {self.label}") from exc
