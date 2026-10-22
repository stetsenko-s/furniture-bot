"""Переходы и HTTP новых этапов: без Telegram и платных обращений к AI."""
import asyncio
import base64
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestServer, TestClient
from bot.main import Application
from bot.stage_client import StageClient, StageError
from bot.store import Store
from bot.workflow_store import WorkflowStore
from contracts.models import ReferenceImage, ConversationMessage
from contracts.workflow import Dimension, StagePlan, StageAnalysisRequest, StageImageRequest
from infrastructure.stage_ai import AIError, StageAI
from infrastructure.stage_config import stage_settings
from infrastructure.stage_http import create_stage_app
from services.dimensions.src.provider import DimensionsProvider, checked_plan
from services.render.src.provider import RenderProvider
from tests.test_bot import FakeMessage, FakeDialogue, FakeSketch, callback
from tests.test_project import PNG, completed
from tests.drawing_fixture import simple_drawing


def reference():
    return ReferenceImage(mime_type='image/png', data_base64=base64.b64encode(PNG).decode())


def plan():
    brief = completed().brief.model_copy(update={'material': 'ЛДСП', 'color': 'белый'})
    dims = [Dimension(id='overall_' + axis, target=axis, axis=axis,
                      value_mm=getattr(brief, axis + '_mm'), source_quote=None)
            for axis in ('width', 'height', 'depth')]
    dims.append(Dimension(id='shelf_width', target='Полка слева', axis='width',
                          value_mm=400, source_quote='Ширина полки 400 мм'))
    result = StagePlan(brief=brief, dimensions=dims, inventory_complete=True,
                     needs_sketch_changes=False, questions=[], reply='Данные согласованы.')
    result.drawing = simple_drawing(result)
    return result


def analysis(stage='dimensions', previous=None, text='Ширина полки 400 мм'):
    return StageAnalysisRequest(stage=stage, brief=plan().brief, plan=previous,
        messages=[{'role': 'user', 'text': text}], references=[reference()])


class PlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_or_unsupported_numbers_block_generation(self):
        result = checked_plan(plan(), analysis(text='Полка'))
        self.assertIsNone(result.dimensions[-1].value_mm)
        self.assertTrue(result.questions)
        with self.assertRaises(ValueError):
            DimensionsProvider.validate_generation(result)

    async def test_exact_user_quote_accepts_decimal_and_preserves_other_values(self):
        value = plan()
        value.dimensions[-1].value_mm = 400.5
        value.dimensions[-1].source_quote = 'Ширина полки 400,5 мм'
        result = checked_plan(value, analysis(text='Ширина полки 400,5 мм'))
        self.assertFalse(result.questions)
        DimensionsProvider.validate_generation(result)
        edited = result.model_copy(deep=True)
        edited.dimensions = edited.dimensions[:3]
        kept = checked_plan(edited, analysis(previous=result, text='Цвет белый'))
        self.assertEqual(kept.dimensions[-1].value_mm, 400.5)

    async def test_assistant_claim_is_not_numeric_evidence(self):
        request = analysis(text='Полка')
        request.messages.append(ConversationMessage(role='assistant', text='Ширина полки 400 мм'))
        self.assertIsNone(checked_plan(plan(), request).dimensions[-1].value_mm)

    async def test_duplicate_inconsistent_and_oversized_dimensions_block(self):
        for change in ('duplicate', 'oversized', 'brief', 'inventory', 'structure'):
            value = plan()
            if change == 'duplicate':
                value.dimensions.append(value.dimensions[-1].model_copy())
            elif change == 'oversized':
                value.dimensions[-1].value_mm = 1200
                value.dimensions[-1].source_quote = 'Ширина полки 1200 мм'
            elif change == 'brief':
                value.brief.width_mm = 999
            elif change == 'inventory':
                value.inventory_complete = False
            else:
                value.needs_sketch_changes = True
            result = checked_plan(value, analysis(text='Ширина полки 400 мм. Ширина полки 1200 мм'))
            with self.subTest(change=change), self.assertRaises(ValueError):
                DimensionsProvider.validate_generation(result)

    async def test_render_preserves_approved_dimensions_and_requires_material(self):
        original = plan()
        changed = original.model_copy(deep=True)
        changed.dimensions[-1].value_mm = 450
        changed.brief.width_mm = 1500
        changed.brief.material = None

        class AI:
            async def analyse(self, *args):
                return changed
        result = await RenderProvider(AI()).analyse(analysis('render', original))
        self.assertEqual(result.dimensions, original.dimensions)
        self.assertEqual(result.brief.width_mm, original.brief.width_mm)
        self.assertGreaterEqual(len(result.questions), 3)
        with self.assertRaises(ValueError):
            RenderProvider.validate_generation(result)

    async def test_stage_model_configuration_is_independent(self):
        settings = {'AI_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'shared',
            'OPENROUTER_DIALOGUE_MODEL': 'old-text', 'OPENROUTER_SKETCH_MODEL': 'old-image',
            'DIMENSIONS_SERVICE_TOKEN': 'internal', 'DIMENSIONS_TEXT_MODEL': 'new-text',
            'DIMENSIONS_IMAGE_MODEL': 'new-image', 'DIMENSIONS_API_KEY': 'own-key'}
        with patch.dict(os.environ, settings, clear=True), patch('infrastructure.stage_config.load_env'):
            ai = stage_settings('dimensions')
        self.assertEqual((ai.key, ai.text_model, ai.image_model), ('own-key', 'new-text', 'new-image'))


class Provider:
    def __init__(self):
        self.calls = 0
        self.failed = False
        self.release = None
        self.started = asyncio.Event()

    async def analyse(self, request):
        value = request.plan.model_copy(deep=True) if request.plan else plan()
        value.questions = []
        if request.messages and 'не знаю' in request.messages[-1].text:
            value.dimensions[-1].value_mm = None
            value.questions = ['Какова ширина полки в мм?']
        return value

    validate_generation = staticmethod(DimensionsProvider.validate_generation)

    async def generate(self, request):
        self.calls += 1
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.failed:
            raise AIError('private-key-and-response')
        return reference(), 'fake-image'


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_reason_is_safe_and_specific(self):
        for code, expected in [('credits', 'средств'), ('incomplete', 'оборвался'),
                               ('invalid_model_response', 'формате'), ('provider_failed', 'не уточнена')]:
            async def fail(request):
                raise AIError('PRIVATE KEY AND PROMPT', code)
            self.provider.analyse = fail
            with self.subTest(code=code), self.assertRaises(StageError) as caught:
                await self.client.analyse(analysis())
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn('PRIVATE', str(caught.exception))
            if code != 'credits':
                self.assertNotIn('баланс', str(caught.exception))

    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provider = Provider()
        self.http = TestClient(TestServer(create_stage_app('dimensions', self.provider, self.temp.name, 'token')))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        self.client = StageClient('dimensions', str(self.http.make_url('/')), 'token')
        self.headers = {'Authorization': 'Bearer token'}
        self.request = StageImageRequest(stage='dimensions', request_id='test', project_id='a'*32,
            revision=1, plan=plan(), references=[reference()])

    async def test_authorization_validation_and_health_without_generation(self):
        self.assertEqual((await self.http.get('/health')).status, 200)
        self.assertEqual((await self.http.post('/v1/images', json=self.request.model_dump())).status, 401)
        for payload in ({'stage': 'render'}, {'contract_version': 2}, {'path': '/private'}):
            response = await self.http.post('/v1/images', json=self.request.model_dump() | payload, headers=self.headers)
            self.assertEqual(response.status, 422)
        self.assertEqual(self.provider.calls, 0)

    async def test_analysis_and_image_roundtrip_cache_and_conflict(self):
        self.assertEqual(await self.client.analyse(analysis()), plan())
        self.assertEqual(await self.client.generate(self.request), (PNG, 'fake-image'))
        self.assertEqual(await self.client.generate(self.request), (PNG, 'fake-image'))
        self.assertEqual(self.provider.calls, 1)
        other = self.request.model_copy(deep=True)
        other.plan.reply = 'Changed payload'
        with self.assertRaises(StageError):
            await self.client.generate(other)
        self.assertEqual(self.provider.calls, 1)

    async def test_parallel_duplicate_rejected(self):
        self.provider.release = asyncio.Event()
        task = asyncio.create_task(self.client.generate(self.request))
        await self.provider.started.wait()
        try:
            with self.assertRaises(StageError):
                await self.client.generate(self.request)
        finally:
            self.provider.release.set()
            await task
        self.assertEqual(self.provider.calls, 1)

    async def test_missing_cached_file_does_not_regenerate_until_explicit_retry(self):
        await self.client.generate(self.request)
        next(Path(self.temp.name).glob('*.png')).unlink()
        with self.assertRaises(StageError):
            await self.client.generate(self.request)
        self.assertEqual(self.provider.calls, 1)
        await self.client.generate(self.request)
        self.assertEqual(self.provider.calls, 2)

    async def test_failure_and_restart_retry_without_leaking_provider_body(self):
        self.provider.failed = True
        with self.assertRaises(StageError) as error:
            await self.client.generate(self.request)
        self.assertNotIn('private', str(error.exception))
        self.provider.failed = False
        await self.client.generate(self.request)
        await self.http.close()
        self.http = TestClient(TestServer(create_stage_app('dimensions', self.provider, self.temp.name, 'token')))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        self.client = StageClient('dimensions', str(self.http.make_url('/')), 'token')
        await self.client.generate(self.request)
        self.assertEqual(self.provider.calls, 2)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeMessage.bots = {}
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bot')
        self.providers, clients = {}, {}
        for stage in ('dimensions', 'render'):
            self.providers[stage] = Provider()
            server = TestServer(create_stage_app(stage, self.providers[stage], Path(self.temp.name) / stage, 'token'))
            await server.start_server()
            self.addAsyncCleanup(server.close)
            clients[stage] = StageClient(stage, str(server.make_url('/')), 'token')
        self.app = Application(self.store, FakeDialogue(), FakeSketch(), clients['dimensions'], clients['render'])
        self.db = self.app.workflow.db
        self.message = FakeMessage('Тумба', image=True)
        await self.app.receive(self.message)
        self.pid = self.store.session(10)['project_id']
        self.rev = self.store.project(10, self.pid)['revision']
        await self.click('generate', self.rev)

    async def click(self, action, *parts):
        return await callback(self.app, self.message, ':'.join([action, self.pid] + [str(p) for p in parts]))

    async def dimensions(self):
        await self.click('approve_sketch', self.rev)
        await self.click('dimensions', self.rev)
        return self.db.version(10, self.pid)['number']

    async def ready_render(self):
        n = await self.dimensions()
        await self.click('wfgen', 'd', n)
        await self.click('wfapprove', 'd', n)
        await self.click('render', n)
        return self.db.version(10, self.pid)['number']

    async def test_complete_pipeline_with_independent_http_services_and_gallery(self):
        sends = len([e for e in self.message.bot.events if e[0] == 'send'])
        n = await self.ready_render()
        await self.click('wfgen', 'r', n)
        await self.click('wfapprove', 'r', n)
        self.assertTrue(self.db.version(10, self.pid)['approved'])
        self.assertEqual({x['stage'] for x in self.db.drawings(10, self.pid)}, {'sketch', 'dimensions', 'render'})
        await self.click('history')
        await self.click('stageview', 'd', 1, 0)
        self.assertEqual(self.db.state(10, self.pid)['stage'], 'render')
        self.assertEqual(len([e for e in self.message.bot.events if e[0] == 'send']), sends)
        reopened = WorkflowStore(Store(self.store.directory))
        self.assertTrue(reopened.version(10, self.pid)['approved'])
        self.assertEqual([p.calls for p in self.providers.values()], [1, 1])

    async def test_cannot_skip_approval_and_stale_buttons_do_not_generate(self):
        with self.assertRaises(ValueError):
            self.db.begin(10, self.pid, 'dimensions', self.rev)
        n = await self.dimensions()
        with self.assertRaises(ValueError):
            self.db.begin(10, self.pid, 'render', n)
        await self.app.receive(FakeMessage('Ширина полки 400 мм'))
        await self.click('wfgen', 'd', n)
        self.assertEqual(self.providers['dimensions'].calls, 0)
        self.assertGreater(self.db.version(10, self.pid)['number'], n)

    async def test_unknown_dimension_blocks_generation(self):
        await self.dimensions()
        await self.app.receive(FakeMessage('не знаю'))
        n = self.db.version(10, self.pid)['number']
        await self.click('wfgen', 'd', n)
        self.assertEqual(self.providers['dimensions'].calls, 0)
        self.assertIsNone(self.db.version(10, self.pid)['path'])

    async def test_edit_after_approval_invalidates_next_stage_and_keeps_archive(self):
        await self.ready_render()
        self.db.back(10, self.pid)
        await self.app.receive(FakeMessage('Ширина полки 400 мм'))
        v = self.db.version(10, self.pid)
        self.assertFalse(v['approved'])
        self.assertIsNone(v['path'])
        with self.assertRaises(ValueError):
            self.db.begin(10, self.pid, 'render', v['number'])
        self.assertEqual(len(self.db.drawings(10, self.pid)), 2)
        await self.click('wfgen', 'd', v['number'])
        await self.click('wfapprove', 'd', v['number'])
        await self.click('render', v['number'])
        new = self.db.version(10, self.pid)
        self.assertEqual(new['source_number'], v['number'])
        self.assertIsNone(new['path'])

    async def test_input_during_generation_keeps_old_image_only_in_archive(self):
        n = await self.dimensions()
        provider = self.providers['dimensions']
        provider.release = asyncio.Event()
        generating = asyncio.create_task(self.click('wfgen', 'd', n))
        await provider.started.wait()
        incoming = asyncio.create_task(self.app.receive(FakeMessage('Ширина полки 400 мм')))
        for _ in range(100):
            if self.db.version(10, self.pid)['number'] != n:
                break
            await asyncio.sleep(.01)
        provider.release.set()
        await asyncio.gather(generating, incoming)
        self.assertNotEqual(self.db.version(10, self.pid)['number'], n)
        self.assertIsNone(self.db.version(10, self.pid)['path'])
        self.assertIsNotNone(self.db.version(10, self.pid, 'dimensions', n)['path'])

    async def test_back_during_generation_does_not_reopen_old_screen(self):
        n = await self.dimensions()
        provider = self.providers['dimensions']
        provider.release = asyncio.Event()
        task = asyncio.create_task(self.click('wfgen', 'd', n))
        await provider.started.wait()
        await self.click('wfback', 'd', n)
        provider.release.set()
        await task
        self.assertEqual(self.db.state(10, self.pid)['stage'], 'sketch')
        self.assertEqual(len(self.db.drawings(10, self.pid)), 2)

    async def test_delete_during_generation_discards_late_result(self):
        n = await self.dimensions()
        provider = self.providers['dimensions']
        provider.release = asyncio.Event()
        task = asyncio.create_task(self.click('wfgen', 'd', n))
        await provider.started.wait()
        self.store.delete(10, self.pid)
        provider.release.set()
        await task
        with self.assertRaises(LookupError):
            self.db.version(10, self.pid)
        with self.store.connect() as con:
            for table in ('workflow_state', 'workflow_versions', 'workflow_messages'):
                self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertFalse((self.store.directory / 'results' / self.pid).exists())

    async def test_request_is_frozen_after_rename_and_ownership_checked(self):
        n = await self.dimensions()
        request = self.db.request(10, self.pid, 'dimensions', n)
        self.store.rename(10, self.pid, 'Новое имя')
        self.assertEqual(self.db.request(10, self.pid, 'dimensions', n), request)
        for call in (lambda: self.db.state(11, self.pid),
                     lambda: self.db.approve_sketch(11, self.pid, self.rev),
                     lambda: self.db.drawings(11, self.pid)):
            with self.assertRaises(LookupError):
                call()

    async def test_rebuild_legacy_drawing_keeps_image_and_history(self):
        n = await self.dimensions()
        await self.click('wfgen', 'd', n)
        old = self.db.version(10, self.pid)
        legacy = old['plan'].model_copy(deep=True)
        legacy.drawing = None
        with self.store.connect() as con:
            con.execute('UPDATE workflow_versions SET plan=? WHERE project_id=? AND stage=? AND number=?',
                        (legacy.model_dump_json(), self.pid, 'dimensions', n))
        messages_before = self.db.messages(10, self.pid, old)
        self.assertTrue(self.db.upgrade_drawing(10, self.pid))
        new = self.db.version(10, self.pid)
        self.assertGreater(new['number'], n)
        self.assertIsNone(new['path'])
        self.assertIsNone(new['request_json'])
        self.assertFalse(new['analysed'])
        self.assertEqual(new['plan'].dimensions, legacy.dimensions)
        self.assertEqual(self.db.messages(10, self.pid, new), messages_before)
        self.assertEqual(self.db.version(10, self.pid, 'dimensions', n)['path'], old['path'])
        self.assertTrue(Path(old['path']).is_file())
        self.assertFalse(self.db.upgrade_drawing(10, self.pid))

    async def test_retry_after_service_failure_and_cross_stage_callback(self):
        n = await self.dimensions()
        self.providers['dimensions'].failed = True
        await self.click('wfgen', 'd', n)
        self.assertTrue(self.db.version(10, self.pid)['error'])
        self.providers['dimensions'].failed = False
        await self.click('wfretry', 'd', n)
        self.assertIsNotNone(self.db.version(10, self.pid)['path'])
        await self.click('wfapprove', 'd', n)
        await self.click('render', n)
        await self.click('wfgen', 'd', n)
        self.assertEqual(self.providers['render'].calls, 0)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_provider_protocols_include_references_and_distinct_models(self):
        for provider in ('openrouter', 'openai'):
            ai = StageAI('key', 'text-model', 'image-model', provider=provider)
            sent = []
            async def fake_post(endpoint, payload, timeout):
                sent.append(payload)
                if payload['model'] == 'image-model':
                    return {'data': [{'b64_json': reference().data_base64}]}
                if provider == 'openrouter':
                    return {'choices': [{'finish_reason': 'stop', 'message': {'content': plan().model_dump_json()}}]}
                return {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': plan().model_dump_json()}]}]}
            ai.post = fake_post
            self.assertEqual(await ai.analyse('rules', {}, [reference()], StagePlan), plan())
            self.assertEqual((await ai.image('rules', plan(), [reference()]))[0], reference())
            self.assertEqual(sent[0]['model'], 'text-model')
            self.assertTrue(sent[1].get('images') or sent[1].get('input_references'))

    async def test_refusal_truncation_and_invalid_image_rejected(self):
        ai = StageAI('key', 'text', 'image', provider='openrouter')
        for result in ({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
                       {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]},
                       {'choices': []}):
            async def post(*args):
                return result
            ai.post = post
            with self.assertRaises(AIError):
                await ai.analyse('rules', {}, [reference()], StagePlan)
        async def post(*args):
            return {'data': [{'b64_json': base64.b64encode(b'not PNG').decode()}]}
        ai.post = post
        with self.assertRaises(AIError):
            await ai.image('rules', plan(), [reference()])
