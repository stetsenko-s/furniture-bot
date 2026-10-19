import asyncio
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bot.main import Application, keyboard
from bot.store import Store
from tests.test_bot import FakeMessage, FakeDialogue, FakeSketch, callback
from tests.test_project import PNG, completed


class ManagementStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_deleted_last_number_is_reused_after_restart_per_user(self):
        first, second = self.store.create(10), self.store.create(10)
        self.assertEqual(first['title'], 'Проект №1')
        self.assertEqual(second['title'], 'Проект №2')
        self.assertEqual(self.store.create(11)['title'], 'Проект №1')
        self.store.delete(10, second['id'])
        restarted = Store(self.temp.name)
        self.assertEqual(restarted.create(10)['title'], 'Проект №2')
        self.assertEqual(restarted.create(11)['title'], 'Проект №2')

    def test_deleting_all_projects_restarts_numbering_at_one(self):
        for _ in range(3):
            self.store.create(10)
        for project in self.store.projects(10):
            self.store.delete(10, project['id'])
        self.assertEqual(Store(self.temp.name).create(10)['title'], 'Проект №1')

    def test_number_uses_maximum_existing_number_including_renamed_projects(self):
        first, second, third = [self.store.create(10) for _ in range(3)]
        self.store.rename(10, third['id'], 'Шкаф')
        self.store.delete(10, second['id'])
        new = self.store.create(10)
        self.assertEqual(new['title'], 'Проект №4')
        self.assertEqual(self.store.project(10, first['id'])['title'], 'Проект №1')
        self.assertEqual(self.store.project(10, third['id'])['title'], 'Шкаф')

    def test_previous_version_counter_is_ignored(self):
        with self.store.connect() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS project_counters(
                user_id INTEGER PRIMARY KEY,last_number INTEGER NOT NULL);
                INSERT OR REPLACE INTO project_counters VALUES(10,100);''')
        restarted = Store(self.temp.name)
        self.assertEqual(restarted.create(10)['title'], 'Проект №1')

    def test_migration_assigns_numbers_and_preserves_existing_data(self):
        # Точная схема прежней версии, без columns number/error и counters.
        with TemporaryDirectory() as old:
            con = sqlite3.connect(Path(old) / 'projects.sqlite3')
            con.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,
                title TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO projects(id,user_id,title) VALUES('first',10,'Новый проект');
                INSERT INTO projects(id,user_id,title) VALUES('second',10,'Шкаф');''')
            con.close()
            migrated = Store(old)
            self.assertEqual(migrated.project(10, 'first')['title'], 'Проект №1')
            self.assertEqual(migrated.project(10, 'second')['title'], 'Шкаф')
            self.assertEqual(migrated.create(10)['title'], 'Проект №3')
            self.assertEqual(Store(old).project(10, 'first')['number'], 1)

    def test_rename_and_model_updates_do_not_change_project_name_or_generation_request(self):
        project = self.store.create(10)
        pid = project['id']
        number = self.store.add_input(10, pid, 'Тумба', PNG, 'image/png')
        self.store.save_analysis(10, pid, number, completed())
        self.assertEqual(self.store.project(10, pid)['title'], 'Проект №1')
        request_brief = self.store.generation_brief(10, pid, number)
        self.store.rename(10, pid, ' Моя тумба ')
        self.assertEqual(self.store.generation_brief(10, pid, number), request_brief)
        self.assertEqual(self.store.revision(10, pid)['brief'].title, 'Моя тумба')
        number = self.store.add_input(10, pid, 'Добавь полку')
        self.store.save_analysis(10, pid, number, completed())
        self.assertEqual(Store(self.temp.name).project(10, pid)['title'], 'Моя тумба')

    def test_delete_removes_only_owned_data_and_files(self):
        project = self.store.create(10)
        other = self.store.create(11)
        pid = project['id']
        number = self.store.add_input(10, pid, 'Тумба', PNG, 'image/png')
        self.store.save_analysis(10, pid, number, completed())
        self.store.approve(10, pid, number)
        self.store.save_artifact(10, pid, number, PNG, 'test')
        self.store.add_input(11, other['id'], 'Шкаф', PNG, 'image/png')
        with self.assertRaises(LookupError):
            self.store.delete(11, pid)
        with self.assertRaises(LookupError):
            self.store.rename(11, pid, 'Чужой')
        self.store.delete(10, pid)
        self.assertIsNone(self.store.session(10)['project_id'])
        self.assertEqual(self.store.projects(10), [])
        for folder in ('uploads', 'results'):
            self.assertFalse((Path(self.temp.name) / folder / pid).exists())
        self.assertEqual(len(self.store.attachments(11, other['id'])), 1)
        with self.store.connect() as con:
            for table in ('revisions', 'messages', 'attachments', 'artifacts'):
                self.assertEqual(con.execute(f'SELECT count(*) FROM {table} WHERE project_id=?', (pid,)).fetchone()[0], 0)

    def test_invalid_title_does_not_change_name_or_revision(self):
        project = self.store.create(10)
        for title in ('', '   ', 'x' * 101, 'две\nстроки'):
            with self.assertRaises(ValueError):
                self.store.rename(10, project['id'], title)
        self.assertEqual(self.store.project(10, project['id']), project)

    def test_latest_drawing_remains_available_after_many_input_revisions(self):
        project = self.store.create(10)
        pid = project['id']
        number = self.store.add_input(10, pid, 'Тумба', PNG, 'image/png')
        self.store.save_analysis(10, pid, number, completed())
        self.store.approve(10, pid, number)
        self.store.save_artifact(10, pid, number, PNG, 'test')
        for index in range(25):
            self.store.add_input(10, pid, f'Правка {index}')
        self.assertEqual(self.store.drawings(10, pid)[0]['number'], number)


class ManagementBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeMessage.bots = {}
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.dialogue, self.sketch = FakeDialogue(), FakeSketch()
        self.app = Application(self.store, self.dialogue, self.sketch)

    async def project(self, prepared=False):
        message = FakeMessage('/start')
        await self.app.command(message)
        await callback(self.app, message, 'new')
        if prepared:
            await self.app.receive(FakeMessage('Тумба 1000 × 800 × 500', image=True))
        project = self.store.project(10, self.store.session(10)['project_id'])
        return message, project

    def buttons(self, message):
        return [button.text for row in message.bot.answers[-1][1].inline_keyboard for button in row]

    def sends(self, message):
        return len([event for event in message.bot.events if event[0] == 'send'])

    async def test_dimensions_instructions_visible_on_new_project_and_data_screen(self):
        message, project = await self.project()
        text = message.bot.answers[-1][0]
        self.assertIn('Размеры вводятся обычным сообщением', text)
        self.assertIn('Ширина 1000 мм, высота 800 мм, глубина 500 мм', text)
        await callback(self.app, message, f'summary:{project["id"]}')
        self.assertIn('Размеры вводятся обычным сообщением', message.bot.answers[-1][0])
        await self.app.receive(FakeMessage('Ширина 1000 мм, высота 800 мм, глубина 500 мм'))
        self.assertIn('Ширина 1000 мм', self.dialogue.calls[-1][1][-1]['text'])
        self.assertEqual(self.store.messages(10, project['id'])[0]['text'],
                         'Ширина 1000 мм, высота 800 мм, глубина 500 мм')
        self.assertEqual(self.sends(message), 2)

    async def test_buttons_use_same_message_for_menu_project_rename_delete_and_cancel(self):
        message, project = await self.project()
        pid = project['id']
        mid = self.store.control(10)['message_id']
        self.assertEqual(project['title'], 'Проект №1')
        for action in (f'rename:{pid}', f'cancel:{pid}', f'delete:{pid}', f'cancel:{pid}', f'back:{pid}', f'open:{pid}'):
            await callback(self.app, message, action)
            self.assertEqual(self.store.control(10)['message_id'], mid)
        self.assertEqual(self.sends(message), 1)
        self.assertEqual(self.buttons(message)[-3:], ['Удалить', 'Назад', 'Переименовать'])
        self.assertNotIn('Чертежи', self.buttons(message))
        self.assertNotIn('Попробовать ещё раз', self.buttons(message))

    async def test_rename_does_not_call_llm_or_add_input_and_survives_updates(self):
        message, project = await self.project()
        pid = project['id']
        await callback(self.app, message, f'rename:{pid}')
        await self.app.receive(FakeMessage('Тумба в прихожую'))
        self.assertEqual(self.dialogue.calls, [])
        self.assertEqual(self.store.messages(10, pid), [])
        self.assertEqual(self.store.project(10, pid)['revision'], 1)
        self.assertEqual(self.store.project(10, pid)['title'], 'Тумба в прихожую')
        await self.app.receive(FakeMessage('Добавь полку', image=True))
        self.assertEqual(self.store.project(10, pid)['title'], 'Тумба в прихожую')
        self.assertEqual(self.sends(message), 3)

    async def test_delete_requires_confirmation_and_cancel_preserves_project(self):
        message, project = await self.project()
        pid = project['id']
        await callback(self.app, message, f'confirm_delete:{pid}')
        self.assertEqual(len(self.store.projects(10)), 1)
        await callback(self.app, message, f'delete:{pid}')
        self.assertIn('Проект №1', message.bot.answers[-1][0])
        await callback(self.app, message, f'cancel:{pid}')
        self.assertEqual(len(self.store.projects(10)), 1)
        await callback(self.app, message, f'delete:{pid}')
        await callback(self.app, message, f'confirm_delete:{pid}')
        self.assertEqual(self.store.projects(10), [])
        self.assertEqual(self.store.session(10)['screen'], 'menu')
        self.assertEqual(self.sends(message), 1)

    async def test_generation_history_and_back_do_not_send_messages(self):
        message, project = await self.project(prepared=True)
        pid = project['id']
        await callback(self.app, message, f'generate:{pid}:{project["revision"]}')
        self.assertIn('Чертежи', self.buttons(message))
        self.assertNotIn('Чертёж', self.buttons(message))
        self.assertNotIn('Предыдущие чертежи', self.buttons(message))
        self.assertEqual(message.photos[-1][0], PNG)
        await callback(self.app, message, f'history:{pid}')
        await callback(self.app, message, f'version:{pid}:{project["revision"]}')
        await callback(self.app, message, f'back:{pid}')
        await callback(self.app, message, f'summary:{pid}')
        await callback(self.app, message, f'back:{pid}')
        self.assertEqual(self.store.session(10)['screen'], 'menu')
        self.assertEqual(self.sends(message), 2)

    async def test_gallery_reaches_every_drawing_and_returns_to_same_page_without_generation(self):
        message, project = await self.project(prepared=True)
        pid = project['id']
        for i in range(23):
            number = self.store.add_input(10, pid, f'Правка {i}')
            self.store.save_analysis(10, pid, number, completed())
            self.store.approve(10, pid, number)
            self.store.save_artifact(10, pid, number, PNG, 'offline-test')
        before = self.store.revision(10, pid)
        sends = self.sends(message)
        dialogue_calls = len(self.dialogue.calls)

        def image_buttons():
            return [b for row in message.bot.answers[-1][1].inline_keyboard
                    for b in row if b.callback_data.startswith('version:')]

        # Старый callback отдельной кнопки тоже ведёт в общий список.
        await callback(self.app, message, f'drawing:{pid}')
        first = image_buttons()
        self.assertEqual(len(first), 20)
        self.assertEqual(first[0].text, 'Эскиз №23')
        self.assertIn('Далее →', self.buttons(message))
        await callback(self.app, message, f'history:{pid}:1')
        second = image_buttons()
        self.assertEqual([b.text for b in second], ['Эскиз №3', 'Эскиз №2', 'Эскиз №1'])
        self.assertEqual(len({b.text for b in first + second}), 23)
        self.assertNotIn('Далее →', self.buttons(message))
        await callback(self.app, message, second[-1].callback_data)
        self.assertIn('Эскиз №1', message.bot.answers[-1][0])
        back = message.bot.answers[-1][1].inline_keyboard[0][0]
        self.assertEqual(back.callback_data, f'history:{pid}:1')
        await callback(self.app, message, back.callback_data)
        self.assertEqual([b.text for b in image_buttons()], ['Эскиз №3', 'Эскиз №2', 'Эскиз №1'])
        self.assertEqual(self.store.revision(10, pid), before)
        self.assertEqual(self.sends(message), sends)
        self.assertEqual(len(self.dialogue.calls), dialogue_calls)
        self.assertEqual(self.sketch.calls, [])

    async def test_new_input_gets_one_answer_below_it_and_buttons_edit_that_answer(self):
        message, project = await self.project()
        old_id = self.store.control(10)['message_id']
        incoming = FakeMessage('Ширина 1000 мм, высота 800 мм, глубина 500 мм', image=True)
        await self.app.receive(incoming)
        new_id = self.store.control(10)['message_id']
        self.assertGreater(new_id, incoming.message_id)
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(self.sends(message), 2)  # прогресс и результат в одном ответе
        self.assertIn('Ширина: 1000 мм', message.bot.messages[new_id].text)
        await callback(self.app, message, f'summary:{project["id"]}')
        await callback(self.app, message, f'back:{project["id"]}')
        self.assertEqual(self.store.control(10)['message_id'], new_id)
        self.assertEqual(self.sends(message), 2)

    async def test_input_after_drawing_keeps_old_image_and_sends_text_below_input(self):
        message, project = await self.project(prepared=True)
        await callback(self.app, message, f'generate:{project["id"]}:{project["revision"]}')
        old_id = self.store.control(10)['message_id']
        old_caption = message.bot.messages[old_id].caption
        incoming = FakeMessage('Добавь полку')
        await self.app.receive(incoming)
        new_id = self.store.control(10)['message_id']
        self.assertGreater(new_id, incoming.message_id)
        self.assertFalse(self.store.control(10)['media'])
        self.assertTrue(message.bot.messages[old_id].photo)
        self.assertEqual(message.bot.messages[old_id].caption, old_caption)
        self.assertIn('Добавь полку', message.bot.messages[new_id].text)
        self.assertEqual(self.sends(message), 3)

    async def test_queued_input_is_answered_below_latest_message_without_late_duplicate(self):
        message, project = await self.project()
        self.dialogue.release = asyncio.Event()
        first = asyncio.create_task(self.app.receive(FakeMessage('Тумба', image=True)))
        await self.dialogue.started.wait()
        incoming = FakeMessage('Добавь полку')
        second = asyncio.create_task(self.app.receive(incoming))
        try:
            for _ in range(100):
                if self.store.control(10)['message_id'] > incoming.message_id:
                    break
                await asyncio.sleep(0)
            current_id = self.store.control(10)['message_id']
            self.assertGreater(current_id, incoming.message_id)
        finally:
            self.dialogue.release.set()
            await asyncio.gather(first, second)
        self.assertEqual(self.store.control(10)['message_id'], current_id)
        self.assertEqual(self.sends(message), 3)
        self.assertIn('Добавь полку', message.bot.messages[current_id].text)

    async def test_invalid_attachment_error_is_shown_below_user_message(self):
        message, project = await self.project()
        incoming = FakeMessage(image=True)
        incoming.document.mime_type = 'application/pdf'
        await self.app.receive(incoming)
        self.assertGreater(self.store.control(10)['message_id'], incoming.message_id)
        self.assertIn('JPG, PNG или WebP', message.bot.answers[-1][0])
        self.assertEqual(self.dialogue.calls, [])
        self.assertEqual(self.store.messages(10, project['id']), [])

    async def test_retry_button_only_after_error_and_retries_failed_generator(self):
        message, project = await self.project(prepared=True)
        pid = project['id']
        self.sketch.failed = True
        await callback(self.app, message, f'generate:{pid}:{project["revision"]}')
        self.assertIn('Попробовать ещё раз', self.buttons(message))
        self.sketch.failed = False
        await callback(self.app, message, f'retry:{pid}')
        self.assertEqual(len(self.sketch.calls), 2)
        self.assertNotIn('Попробовать ещё раз', self.buttons(message))

    async def test_delete_during_analysis_does_not_resurrect_project_or_overwrite_menu(self):
        message, project = await self.project()
        pid = project['id']
        self.dialogue.release = asyncio.Event()
        task = asyncio.create_task(self.app.receive(FakeMessage('Тумба', image=True)))
        await self.dialogue.started.wait()
        await callback(self.app, message, f'delete:{pid}')
        await callback(self.app, message, f'confirm_delete:{pid}')
        before = list(message.bot.events)
        self.dialogue.release.set()
        await task
        self.assertEqual(self.store.projects(10), [])
        self.assertEqual(message.bot.events, before)

    async def test_delete_during_generation_discards_late_image(self):
        message, project = await self.project(prepared=True)
        pid = project['id']
        self.sketch.release = asyncio.Event()
        task = asyncio.create_task(callback(self.app, message, f'generate:{pid}:{project["revision"]}'))
        await self.sketch.started.wait()
        await callback(self.app, message, f'delete:{pid}')
        await callback(self.app, message, f'confirm_delete:{pid}')
        before = list(message.bot.events)
        self.sketch.release.set()
        await task
        self.assertEqual(self.store.projects(10), [])
        self.assertFalse((Path(self.temp.name) / 'results' / pid).exists())
        self.assertEqual(message.bot.events, before)

    async def test_rename_screen_not_overwritten_by_late_analysis(self):
        message, project = await self.project()
        pid = project['id']
        self.dialogue.release = asyncio.Event()
        task = asyncio.create_task(self.app.receive(FakeMessage('Тумба', image=True)))
        await self.dialogue.started.wait()
        await callback(self.app, message, f'rename:{pid}')
        self.dialogue.release.set()
        await task
        self.assertEqual(self.store.session(10)['screen'], 'rename')
        self.assertIn('Напишите новое название', message.bot.answers[-1][0])
        await self.app.receive(FakeMessage('Для спальни'))
        self.assertEqual(self.store.project(10, pid)['title'], 'Для спальни')

    async def test_restart_edits_existing_panel(self):
        message, project = await self.project()
        restarted = Application(Store(self.temp.name), FakeDialogue(), FakeSketch())
        await callback(restarted, message, 'menu')
        await callback(restarted, message, f'open:{project["id"]}')
        self.assertEqual(self.sends(message), 1)

    async def test_explicit_start_and_menu_send_visible_card_at_end_of_chat(self):
        message, project = await self.project()
        old_id = self.store.control(10)['message_id']
        for index, command in enumerate(('/start', '/menu'), start=2):
            await self.app.command(FakeMessage(command))
            new_id = self.store.control(10)['message_id']
            self.assertNotEqual(new_id, old_id)
            self.assertEqual(self.sends(message), index)
            self.assertEqual(self.store.session(10)['screen'], 'menu')
            self.assertEqual(len(self.store.projects(10)), 1)
            await callback(self.app, message, f'open:{project["id"]}')
            self.assertEqual(self.sends(message), index)
            old_id = new_id

    async def test_old_panel_buttons_are_rejected_after_new_menu_command(self):
        from types import SimpleNamespace
        message, project = await self.project()
        old_panel = message.bot.messages[self.store.control(10)['message_id']]
        await self.app.command(FakeMessage('/menu'))
        answers = []
        async def answer(text=None):
            answers.append(text)
        await self.app.callback(SimpleNamespace(from_user=SimpleNamespace(id=10), message=old_panel,
            data=f'delete:{project["id"]}', answer=answer))
        self.assertIn('старое меню', answers[-1])
        self.assertEqual(self.store.session(10)['screen'], 'menu')
        self.assertEqual(len(self.store.projects(10)), 1)
        self.assertEqual(self.sends(message), 2)

    async def test_long_text_and_photo_captions_paginate_without_losing_text(self):
        message, project = await self.project()
        text = 'Описание мебели 😀 ' * 600
        await self.app.say(message, 10, text, keyboard([[('Назад', 'menu')]]))
        chunks = [message.bot.answers[-1][0].split('\n\nСтраница')[0]]
        while '▶' in self.buttons(message):
            markup = message.bot.answers[-1][1]
            action = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text == '▶')
            await callback(self.app, message, action)
            chunks.append(message.bot.answers[-1][0].split('\n\nСтраница')[0])
        self.assertEqual(''.join(chunks), text)
        image = Path(self.temp.name) / 'picture.png'
        image.write_bytes(PNG)
        await self.app.say(message, 10, text, image=image)
        self.assertIn('Страница 1', message.bot.answers[-1][0])
        self.assertEqual(self.sends(message), 1)

    async def test_deleted_telegram_panel_button_does_not_send_replacement(self):
        message, project = await self.project()
        control = self.store.control(10)
        target = message.bot.messages.pop(control['message_id'])
        answers = []
        async def answer(text=None):
            answers.append(text)
        from types import SimpleNamespace
        await self.app.callback(SimpleNamespace(from_user=SimpleNamespace(id=10), message=target,
            data='menu', answer=answer))
        self.assertEqual(self.sends(message), 1)
        self.assertIn('/menu', answers[-1])
        await self.app.command(FakeMessage('/menu'))
        self.assertEqual(self.sends(message), 2)
