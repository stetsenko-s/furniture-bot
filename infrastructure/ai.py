"""Выбор API и моделей без передачи ключей между микросервисами."""
from dataclasses import dataclass, field
import os

from .env import require


@dataclass(frozen=True)
class AISettings:
    provider: str
    key: str = field(repr=False)
    dialogue_model: str
    sketch_model: str


def ai_settings():
    # Совместимость с существующим .env: переключение только явно.
    provider = os.environ.get('AI_PROVIDER', 'openai').strip().lower()
    if provider == 'openrouter':
        require('OPENROUTER_API_KEY')
        return AISettings(provider, os.environ['OPENROUTER_API_KEY'],
            os.environ.get('OPENROUTER_DIALOGUE_MODEL') or 'openai/gpt-4.1-mini',
            os.environ.get('OPENROUTER_SKETCH_MODEL') or 'openai/gpt-image-2')
    if provider == 'openai':
        require('OPENAI_API_KEY')
        return AISettings(provider, os.environ['OPENAI_API_KEY'],
            os.environ.get('DIALOGUE_MODEL') or 'gpt-4.1-mini',
            os.environ.get('SKETCH_MODEL') or 'gpt-image-2')
    raise SystemExit('AI_PROVIDER должен быть openai или openrouter.')
