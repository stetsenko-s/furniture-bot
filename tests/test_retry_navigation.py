"""Явный /retry должен отвечать после просмотра архива и выхода в меню."""
import asyncio
import unittest
from tests import test_workflow as fixtures
from tests.test_bot import FakeMessage


class RetryNavigationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkflowTests.asyncSetUp
    click = fixtures.WorkflowTests.click
    dimensions = fixtures.WorkflowTests.dimensions

    async def test_retry_from_archive_history_menu_opens_current_stage_and_answers_below_command(self):
        await self.dimensions()
        provider = self.providers['dimensions']
        original = provider.analyse
        calls = []
        async def record(request):
            calls.append(request)
            return await original(request)
        provider.analyse = record
        for screen in ('archive', 'history', 'menu', 'rename'):
            with self.subTest(screen=screen):
                self.store.navigate(10, screen, self.pid)
                command = FakeMessage('/retry')
                old_sends = len([e for e in command.bot.events if e[0] == 'send'])
                await self.app.command(command)
                self.assertEqual(self.store.session(10)['screen'], 'review')
                self.assertGreater(self.store.control(10)['message_id'], command.message_id)
                self.assertEqual(len([e for e in command.bot.events if e[0] == 'send']), old_sends + 1)
                self.assertIn('Этап:', self.store.control(10)['payload']['text'])
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.providers['dimensions'].calls, 0)

    async def test_retry_from_archived_sketch_does_not_stay_silent(self):
        await self.click('history')
        await self.click('version', self.rev, 0)
        self.assertEqual(self.store.session(10)['screen'], 'archive')
        command = FakeMessage('/retry')
        await self.app.command(command)
        self.assertTrue(self.app.visible(10, self.pid))
        self.assertGreater(self.store.control(10)['message_id'], command.message_id)

    async def test_retry_from_archive_shows_service_error(self):
        await self.dimensions()
        from bot.stage_client import StageError
        async def fail(request):
            raise StageError('Сервис недоступен. Попробуйте позже.')
        self.app.workflow.clients['dimensions'].analyse = fail
        self.store.navigate(10, 'archive', self.pid)
        await self.app.command(FakeMessage('/retry'))
        self.assertIn('Сервис недоступен', self.store.control(10)['payload']['text'])

    async def test_busy_retry_answers_immediately_and_does_not_queue_second_analysis(self):
        await self.dimensions()
        lock = self.app.lock(10)
        await lock.acquire()
        try:
            self.store.navigate(10, 'archive', self.pid)
            await asyncio.wait_for(self.app.command(FakeMessage('/retry')), timeout=1)
            self.assertIn('Обработка уже идёт', self.store.control(10)['payload']['text'])
            self.assertTrue(self.db.version(10, self.pid)['analysed'])
        finally:
            lock.release()

    async def test_navigation_away_during_retry_still_prevents_late_ui_jump(self):
        await self.dimensions()
        started, release = asyncio.Event(), asyncio.Event()
        original = self.providers['dimensions'].analyse
        async def delayed(request):
            started.set()
            await release.wait()
            return await original(request)
        self.providers['dimensions'].analyse = delayed
        task = asyncio.create_task(self.app.command(FakeMessage('/retry')))
        await started.wait()
        try:
            await self.app.menu(self.message, 10)
        finally:
            release.set()
            await task
        self.assertEqual(self.store.session(10)['screen'], 'menu')
        self.assertTrue(self.db.version(10, self.pid)['analysed'])
