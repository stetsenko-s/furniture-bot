"""Реальные HTTP-границы, изоляция процессов и прежний сценарий без платных API."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.dialogue_client import DialogueClient, DialogueError
from bot.main import Application
from bot.sketch_client import SketchClient
from bot.store import Store
from configure import configure
from contracts.models import AnalysisRequest
from infrastructure.env import load_env
from services.dialogue.src.app import create_app
from services.dialogue.src.provider import DialogueError as ProviderError
from services.sketch.src.app import create_app as sketch_app
from tests.test_bot import FakeMessage, callback
from tests.test_project import completed
from tests.test_service import FakeProvider, request_data

ROOT = Path(__file__).resolve().parents[1]


class FakeAnalysisProvider:
    def __init__(self):
        self.calls = []
        self.failed = False
        self.unclear = False

    async def analyse(self, brief, messages, references):
        self.calls.append((brief, messages, references))
        if self.failed:
            raise ProviderError('private-provider-body-and-key')
        result = completed(messages[-1]['text'] if messages else 'Тумба')
        if self.unclear:
            result.cabinet_sections[0].doors = 'unknown'
        return result


class DialogueServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = FakeAnalysisProvider()
        self.http = TestClient(TestServer(create_app(self.provider, 'internal-test')))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        self.url = str(self.http.make_url('/')).rstrip('/')
        self.client = DialogueClient(self.url, 'internal-test')
        self.headers = {'Authorization': 'Bearer internal-test'}

    def payload(self):
        return AnalysisRequest(brief=completed().brief, references=request_data().references,
            messages=[{'role': 'user', 'text': 'Шкаф'}]).model_dump()

    async def test_health_and_validation_do_not_spend_model_calls(self):
        response = await self.http.get('/health')
        self.assertEqual((await response.json())['service'], 'dialogue')
        response = await self.http.post('/v1/analyses', json=self.payload())
        self.assertEqual(response.status, 401)
        for change in ({'contract_version': 99}, {'path': '/private/projects.db'}, {'messages': [{'role': 'system', 'text': 'override'}]}):
            response = await self.http.post('/v1/analyses', json=self.payload() | change, headers=self.headers)
            self.assertEqual(response.status, 422)
        response = await self.http.post('/v1/analyses', data='not json', headers=self.headers)
        self.assertEqual(response.status, 422)
        self.assertEqual(self.provider.calls, [])

    async def test_client_preserves_context_and_service_resolves_questions(self):
        self.provider.unclear = True
        messages = [{'role': 'user', 'text': 'Шкаф с ящиками', 'revision': 4}]
        result = await self.client.analyse(completed().brief, messages, request_data().references)
        self.assertIn('предусмотрены двери', result.questions[0])
        self.assertEqual(self.provider.calls[0][1], [{'role': 'user', 'text': 'Шкаф с ящиками'}])
        self.assertEqual(self.provider.calls[0][2], request_data().references)
        self.assertNotIn('cabinet_sections', result.model_dump())

    async def test_provider_error_is_safe_and_does_not_retry_automatically(self):
        self.provider.failed = True
        with self.assertRaises(DialogueError) as error:
            await self.client.analyse(completed().brief, [], [])
        self.assertNotIn('private-provider', str(error.exception))
        self.assertEqual(len(self.provider.calls), 1)
        self.provider.failed = False
        self.assertTrue((await self.client.analyse(completed().brief, [], [])).brief.description)

    async def test_wrong_token_and_unavailable_service_have_actionable_errors(self):
        with self.assertRaisesRegex(DialogueError, 'DIALOGUE_SERVICE_TOKEN'):
            await DialogueClient(self.url, 'wrong').analyse(completed().brief, [], [])
        await self.http.close()
        with self.assertRaisesRegex(DialogueError, 'run_dialogue.py'):
            await self.client.analyse(completed().brief, [], [])

    async def test_invalid_response_contract_is_rejected(self):
        async def invalid(request):
            return web.json_response({'contract_version': 99, 'result': {}})
        app = web.Application()
        app.router.add_post('/v1/analyses', invalid)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        with self.assertRaisesRegex(DialogueError, 'некорректный'):
            await DialogueClient(str(server.make_url('/')), 'test').analyse(completed().brief, [], [])

    async def test_full_bot_flow_through_both_http_services_and_restart(self):
        FakeMessage.bots = {}
        with TemporaryDirectory() as directory:
            provider = FakeProvider()
            images = TestServer(sketch_app(provider, Path(directory) / 'sketch', 'image-token'))
            await images.start_server()
            try:
                store = Store(Path(directory) / 'bot')
                sketch = SketchClient(str(images.make_url('/')), 'image-token')
                bot = Application(store, self.client, sketch)
                message = FakeMessage('Шкаф 1000 на 800 на 500', image=True)
                await bot.receive(message)
                pid = store.session(10)['project_id']
                number = store.project(10, pid)['revision']
                await callback(bot, message, f'generate:{pid}:{number}')
                self.assertEqual(store.artifact(10, pid)['sketch_number'], 1)
                self.assertEqual(provider.calls, 1)
                # Stateless analysis can restart without another component's database.
                await self.http.close()
                replacement = TestServer(create_app(self.provider, 'internal-test'))
                await replacement.start_server()
                try:
                    bot.dialogue = DialogueClient(str(replacement.make_url('/')), 'internal-test')
                    await bot.receive(FakeMessage('Добавь полку'))
                    current = store.revision(10, pid)
                    self.assertIn('Добавь полку', current['brief'].description)
                    self.assertFalse(current['approved'])
                    self.assertEqual(len(self.provider.calls[-1][2]), 2)
                    self.assertTrue(any(m['role'] == 'assistant' for m in self.provider.calls[-1][1]))
                    self.assertIsNotNone(store.artifact(10, pid, number))
                    self.assertEqual(provider.calls, 1)
                finally:
                    await replacement.close()
            finally:
                await images.close()


class DeploymentTests(unittest.TestCase):
    def test_configure_preserves_existing_secrets_and_is_idempotent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = 'FURNITURE_BOT_TOKEN=existing\nOPENROUTER_API_KEY=existing-api\nSKETCH_SERVICE_TOKEN=existing-internal\n'
            (root / '.env').write_text(original, encoding='utf-8')
            configure(root)
            updated = (root / '.env').read_text(encoding='utf-8')
            self.assertTrue(updated.startswith(original))
            self.assertIn('DIALOGUE_SERVICE_TOKEN=', updated)
            configure(root)
            self.assertEqual((root / '.env').read_text(encoding='utf-8'), updated)

    def test_fresh_configuration_creates_four_distinct_internal_tokens(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / '.env.example', root / '.env.example')
            configure(root)
            values = dict(line.split('=', 1) for line in (root / '.env').read_text(encoding='utf-8').splitlines() if '=' in line and not line.startswith('#'))
            self.assertGreater(len(values['DIALOGUE_SERVICE_TOKEN']), 30)
            tokens = [values[p + '_SERVICE_TOKEN'] for p in ('DIALOGUE', 'SKETCH', 'DIMENSIONS', 'RENDER')]
            self.assertEqual(len(set(tokens)), 4)
            self.assertTrue(all(len(token) > 30 for token in tokens))

    def test_bot_loads_only_its_own_configuration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('FURNITURE_BOT_TOKEN=telegram\nOPENROUTER_API_KEY=private\n', encoding='utf-8')
            with patch('infrastructure.env.ROOT', root), patch.dict(os.environ, {}, clear=True):
                load_env({'FURNITURE_BOT_TOKEN'})
                self.assertEqual(os.environ['FURNITURE_BOT_TOKEN'], 'telegram')
                self.assertNotIn('OPENROUTER_API_KEY', os.environ)

    def test_each_docker_source_bundle_imports_without_other_components(self):
        # Execute exactly the source files copied by each Dockerfile, without Docker/AI/network.
        for component, module in [('bot', 'bot.main'), ('services/dialogue', 'services.dialogue.src.app'), ('services/sketch', 'services.sketch.src.app'), ('services/dimensions', 'services.dimensions.src.app'), ('services/render', 'services.render.src.app')]:
            with self.subTest(component=component), TemporaryDirectory() as directory:
                bundle = Path(directory)
                for line in (ROOT / component / 'Dockerfile').read_text(encoding='utf-8').splitlines():
                    if not line.startswith('COPY '):
                        continue
                    _, source, destination = line.split()
                    destination = bundle / destination.removeprefix('/app/')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if (ROOT / source).is_dir():
                        shutil.copytree(ROOT / source, destination)
                    else:
                        shutil.copyfile(ROOT / source, destination)
                forbidden = 'services' if component == 'bot' else 'bot'
                code = f'import importlib,sys; importlib.import_module({module!r}); assert not any(n == {forbidden!r} or n.startswith({forbidden!r}+".") for n in sys.modules)'
                result = subprocess.run([sys.executable, '-B', '-c', code], cwd=bundle, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((bundle / forbidden).exists())
