import base64
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from services.dialogue.src.provider import DialogueClient, DialogueError
from infrastructure.ai import ai_settings
from infrastructure.env import load_env
from services.sketch.src.provider import ImageProvider, ProviderError
from tests.test_project import PNG, completed
from tests.test_service import request_data


class ConfigurationTests(unittest.TestCase):
    def test_openrouter_requires_only_its_own_key(self):
        with patch.dict(os.environ, {'AI_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'router-key'}, clear=True):
            config = ai_settings()
            self.assertEqual(config.provider, 'openrouter')
            self.assertEqual(config.dialogue_model, 'openai/gpt-4.1-mini')
            self.assertEqual(config.sketch_model, 'openai/gpt-image-2')
            self.assertNotIn('router-key', repr(config))

    def test_legacy_openai_environment_keeps_existing_models(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'old-key', 'DIALOGUE_MODEL': 'old-dialogue',
                                    'SKETCH_MODEL': 'old-image'}, clear=True):
            config = ai_settings()
            self.assertEqual(config.provider, 'openai')
            self.assertEqual(config.dialogue_model, 'old-dialogue')
            self.assertEqual(config.sketch_model, 'old-image')

    def test_router_models_do_not_inherit_unprefixed_openai_models(self):
        with patch.dict(os.environ, {'AI_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'key',
                'DIALOGUE_MODEL': 'legacy', 'SKETCH_MODEL': 'legacy',
                'OPENROUTER_DIALOGUE_MODEL': 'vendor/dialogue', 'OPENROUTER_SKETCH_MODEL': 'vendor/image'}, clear=True):
            config = ai_settings()
            self.assertEqual(config.dialogue_model, 'vendor/dialogue')
            self.assertEqual(config.sketch_model, 'vendor/image')

    def test_missing_key_and_unknown_provider_stop_with_clear_configuration_error(self):
        for environment, expected in [({'AI_PROVIDER': 'openrouter'}, 'OPENROUTER_API_KEY'),
                                       ({'AI_PROVIDER': 'invalid'}, 'AI_PROVIDER')]:
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(SystemExit) as error:
                    ai_settings()
                self.assertIn(expected, str(error.exception))

    def test_dotenv_reads_router_settings_without_overwriting_system_environment(self):
        with TemporaryDirectory() as directory:
            Path(directory, '.env').write_text('AI_PROVIDER=openrouter\nOPENROUTER_API_KEY=local-key\n'
                'OPENROUTER_DIALOGUE_MODEL=vendor/dialogue\nOPENROUTER_SKETCH_MODEL=vendor/image\n', encoding='utf-8')
            with patch('infrastructure.env.ROOT', Path(directory)), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'system-key'}, clear=True):
                load_env()
                config = ai_settings()
                self.assertEqual(config.key, 'system-key')
                self.assertEqual(config.sketch_model, 'vendor/image')


class OpenRouterClientTests(unittest.IsolatedAsyncioTestCase):
    async def endpoint(self, handler):
        app = web.Application()
        app.router.add_post('/api', handler)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return str(server.make_url('/api'))

    async def test_dialogue_sends_schema_and_private_sketch_with_current_history(self):
        async def handler(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer router-key')
            payload = await request.json()
            self.assertEqual(payload['model'], 'openai/gpt-4.1-mini')
            self.assertEqual(payload['messages'][0]['role'], 'system')
            content = payload['messages'][1]['content']
            self.assertIn('Добавь полку', content[0]['text'])
            self.assertEqual(content[1]['image_url']['url'], request_data().references[0].data_url())
            self.assertTrue(payload['provider']['require_parameters'])
            schema = payload['response_format']['json_schema']
            self.assertTrue(schema['strict'])
            self.assertEqual(payload['response_format']['type'], 'json_schema')
            self.assertFalse(payload['stream'])
            return web.json_response({'choices': [{'finish_reason': 'stop',
                'message': {'content': completed('Тумба с полкой').model_dump_json()}}]})
        endpoint = await self.endpoint(handler)
        result = await DialogueClient('router-key', 'openai/gpt-4.1-mini', endpoint,
            provider='openrouter').analyse(completed().brief, [{'role': 'user', 'text': 'Добавь полку'}],
                                           request_data().references)
        self.assertEqual(result.brief.description, 'Тумба с полкой')

    async def test_image_api_uses_input_references_and_returns_one_png(self):
        async def handler(request):
            payload = await request.json()
            self.assertEqual(payload['model'], 'openai/gpt-image-2')
            self.assertEqual(payload['n'], 1)
            self.assertEqual(payload['output_format'], 'png')
            self.assertEqual(payload['input_references'][0], {'type': 'image_url',
                'image_url': {'url': request_data().references[0].data_url()}})
            self.assertNotIn('images', payload)
            self.assertIn('НЕ наноси размерные', payload['prompt'])
            return web.json_response({'data': [{'b64_json': base64.b64encode(PNG).decode(), 'media_type': 'image/png'}]})
        endpoint = await self.endpoint(handler)
        image, model = await ImageProvider('router-key', 'openai/gpt-image-2', 'medium', '1536x1024',
            endpoint, provider='openrouter').generate(request_data())
        self.assertEqual(base64.b64decode(image.data_base64), PNG)
        self.assertEqual(model, 'openai/gpt-image-2')

    async def test_empty_refused_incomplete_and_invalid_dialogue_answers_are_safe_errors(self):
        for data in ({'choices': []}, {'error': {'message': 'private-payload'}},
                     {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
                     {'choices': [{'finish_reason': 'stop', 'message': {'refusal': 'private-payload'}}]},
                     {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}, []):
            async def handler(request, result=data):
                return web.json_response(result)
            endpoint = await self.endpoint(handler)
            with self.assertRaises(DialogueError) as error:
                await DialogueClient('key', 'model', endpoint, provider='openrouter').analyse(completed().brief, [], [])
            self.assertNotIn('private-payload', str(error.exception))

    async def test_invalid_image_answers_are_safe_errors(self):
        for data in ({'data': []}, {'error': {'message': 'private-payload'}},
                     {'data': [{'media_type': 'image/jpeg', 'b64_json': base64.b64encode(PNG).decode()}]},
                     {'data': [{'media_type': 'image/png', 'b64_json': base64.b64encode(b'not a PNG').decode()}]}, []):
            async def handler(request, result=data):
                return web.json_response(result)
            endpoint = await self.endpoint(handler)
            with self.assertRaises(ProviderError) as error:
                await ImageProvider('key', 'model', 'medium', '1536x1024', endpoint,
                    provider='openrouter').generate(request_data())
            self.assertNotIn('private-payload', str(error.exception))

    async def test_insufficient_balance_errors_do_not_leak_key_or_response_body(self):
        calls = []
        async def handler(request):
            calls.append(1)
            return web.json_response({'error': {'message': 'secret-key-123'}}, status=402)
        endpoint = await self.endpoint(handler)
        for operation, kind in ((DialogueClient('secret-key-123', 'model', endpoint,
                                provider='openrouter').analyse(completed().brief, [], []), DialogueError),
                               (ImageProvider('secret-key-123', 'model', 'medium', '1536x1024', endpoint,
                                provider='openrouter').generate(request_data()), ProviderError)):
            with self.assertRaises(kind) as error:
                await operation
            self.assertIn('OpenRouter', str(error.exception))
            self.assertIn('402', str(error.exception))
            self.assertNotIn('secret-key-123', str(error.exception))
        self.assertEqual(len(calls), 2)  # клиент не делает скрытых повторов
