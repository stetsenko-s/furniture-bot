"""Независимый сервис уточнения заявки: контекст на входе, описание на выходе."""
import hmac
import logging
import os

from aiohttp import web
from pydantic import ValidationError

from contracts.models import AnalysisRequest, AnalysisResponse
from infrastructure.ai import ai_settings
from infrastructure.env import load_env, require
from .provider import DialogueClient, DialogueError
from .rules import analysis_result

LOG = logging.getLogger(__name__)


def create_app(provider, token):
    if not token:
        raise ValueError('Нужен DIALOGUE_SERVICE_TOKEN.')
    app = web.Application(client_max_size=60 * 1024 * 1024)

    async def health(request):
        return web.json_response({'service': 'dialogue', 'contract_version': 1, 'status': 'ok'})

    async def analyse(request):
        authorization = request.headers.get('Authorization', '')
        if not hmac.compare_digest(authorization.encode(), ('Bearer ' + token).encode()):
            return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            payload = AnalysisRequest.model_validate(await request.json())
        except (ValidationError, ValueError):
            return web.json_response({'error': 'invalid_request'}, status=422)
        messages = [message.model_dump() for message in payload.messages]
        try:
            result = await provider.analyse(payload.brief, messages, payload.references)
        except DialogueError:
            LOG.warning('Анализ не завершён: ошибка провайдера')
            return web.json_response({'error': 'provider_failed'}, status=502)
        response = AnalysisResponse(result=analysis_result(result, messages))
        return web.json_response(response.model_dump())

    app.router.add_get('/health', health)
    app.router.add_post('/v1/analyses', analyse)
    return app


def main():
    load_env({'AI_PROVIDER', 'OPENROUTER_API_KEY', 'OPENROUTER_DIALOGUE_MODEL',
              'OPENAI_API_KEY', 'DIALOGUE_MODEL', 'DIALOGUE_SERVICE_TOKEN',
              'DIALOGUE_HOST', 'DIALOGUE_PORT'})
    require('DIALOGUE_SERVICE_TOKEN')
    ai = ai_settings()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    provider = DialogueClient(ai.key, ai.dialogue_model, provider=ai.provider)
    web.run_app(create_app(provider, os.environ['DIALOGUE_SERVICE_TOKEN']),
                host=os.environ.get('DIALOGUE_HOST', '127.0.0.1'),
                port=int(os.environ.get('DIALOGUE_PORT', '8092')), access_log=None)


if __name__ == '__main__':
    main()
