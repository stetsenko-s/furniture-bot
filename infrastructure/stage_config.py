"""Каждый этап получает собственные модели и ключи с локальными значениями по умолчанию."""
import os
from .env import load_env, require
from .stage_ai import StageAI


def stage_settings(stage):
    prefix = stage.upper()
    names = ('AI_PROVIDER', 'API_KEY', 'TEXT_MODEL', 'IMAGE_MODEL', 'QUALITY', 'SIZE', 'SERVICE_TOKEN', 'HOST', 'PORT')
    load_env({prefix + '_' + name for name in names} | {
        'AI_PROVIDER', 'OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'OPENROUTER_DIALOGUE_MODEL',
        'OPENROUTER_SKETCH_MODEL', 'DIALOGUE_MODEL', 'SKETCH_MODEL', 'DATA_DIR'})
    require(prefix + '_SERVICE_TOKEN')
    provider = os.environ.get(prefix + '_AI_PROVIDER') or os.environ.get('AI_PROVIDER', 'openai')
    if provider not in ('openai', 'openrouter'):
        raise SystemExit(f'Проверьте {prefix}_AI_PROVIDER')
    router = provider == 'openrouter'
    key = os.environ.get(prefix + '_API_KEY') or os.environ.get('OPENROUTER_API_KEY' if router else 'OPENAI_API_KEY')
    if not key:
        raise SystemExit(f'Заполните {prefix}_API_KEY либо ключ общего AI_PROVIDER')
    text_model = os.environ.get(prefix + '_TEXT_MODEL') or os.environ.get('OPENROUTER_DIALOGUE_MODEL' if router else 'DIALOGUE_MODEL') or ('openai/gpt-4.1-mini' if router else 'gpt-4.1-mini')
    image_model = os.environ.get(prefix + '_IMAGE_MODEL') or os.environ.get('OPENROUTER_SKETCH_MODEL' if router else 'SKETCH_MODEL') or ('openai/gpt-image-2' if router else 'gpt-image-2')
    return StageAI(key, text_model, image_model, provider=provider,
                   quality=os.environ.get(prefix + '_QUALITY', 'medium'), size=os.environ.get(prefix + '_SIZE', '1536x1024'))
