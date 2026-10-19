from services.dialogue.src.rules import analysis_result
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bot.main import Application
from bot.store import Store
from services.dialogue.src.models import CabinetSection, DialogueResult
from pydantic import ValidationError
from tests import test_bot as fixtures
from tests.test_project import PNG, completed


def section(**updates):
    values = dict(name='Правое отделение', doors='present', no_doors_user_quote=None,
                  door_details_known=True, interior_known=True)
    values.update(updates)
    return CabinetSection(**values)


class ConstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.pid = self.store.create(10)['id']

    def analyse(self, response=None, text='Шкаф', image=PNG):
        number = self.store.add_input(10, self.pid, text, image, 'image/png')
        self.store.save_analysis(10, self.pid, number, analysis_result(response or completed(), self.store.messages(10, self.pid)))
        return number

    def test_unknown_doors_block_even_if_model_returns_no_questions(self):
        response = completed()
        response.cabinet_sections = [section(doors='unknown')]
        number = self.analyse(response)
        self.assertIn('предусмотрены двери', self.store.revision(10, self.pid)['questions'][0])
        with self.assertRaisesRegex(ValueError, 'уточнения'):
            self.store.approve(10, self.pid, number)

    def test_hidden_interior_and_unspecified_door_mechanism_block(self):
        response = completed()
        response.cabinet_sections = [section(interior_known=False, door_details_known=False)]
        number = self.analyse(response)
        questions = self.store.revision(10, self.pid)['questions']
        self.assertEqual(len(questions), 2)
        self.assertIn('как они открываются', questions[0])
        self.assertIn('что находится внутри', questions[1])
        with self.assertRaises(ValueError):
            self.store.approve(10, self.pid, number)

    def test_absence_needs_real_user_quote_not_an_assistant_claim(self):
        for quote in (None, 'дверей нет'):
            response = completed()
            response.reply = 'дверей нет'
            response.cabinet_sections = [section(doors='absent', no_doors_user_quote=quote)]
            number = self.analyse(response)
            with self.assertRaises(ValueError):
                self.store.approve(10, self.pid, number)
        response.cabinet_sections = [section(doors='absent', no_doors_user_quote='дверей нет')]
        number = self.analyse(response, text='Справа дверей нет, отделение пустое.')
        self.store.approve(10, self.pid, number)
        # Явный ответ сохраняет силу после очередного изменения размера.
        number = self.analyse(response, text='Ширина 1200')
        self.store.approve(10, self.pid, number)

    def test_each_section_checked_independently(self):
        response = completed()
        response.cabinet_sections = [section(name='Ящики снизу', doors='absent',
            no_doors_user_quote='Ящики без общей двери'),
            section(name='Над ящиками', doors='unknown')]
        self.analyse(response, text='Ящики без общей двери')
        questions = self.store.revision(10, self.pid)['questions']
        self.assertEqual(len(questions), 1)
        self.assertIn('Над ящиками', questions[0])

    def test_empty_cabinet_review_blocks_but_table_does_not_need_doors(self):
        response = completed()
        response.cabinet_sections = []
        number = self.analyse(response)
        with self.assertRaises(ValueError):
            self.store.approve(10, self.pid, number)
        response.is_cabinet = False
        response.brief.description = 'Стол на четырёх ножках'
        number = self.analyse(response)
        self.store.approve(10, self.pid, number)

    def test_old_model_response_without_review_is_rejected(self):
        data = completed().model_dump()
        del data['cabinet_sections']
        with self.assertRaises(ValidationError):
            DialogueResult.model_validate(data)

    def test_questions_and_numbering_survive_restart_in_conversation(self):
        response = completed()
        response.cabinet_sections = [section(doors='unknown', interior_known=False)]
        response.brief.width_mm = None
        self.analyse(response)
        restarted = Store(self.temp.name)
        text = restarted.messages(10, self.pid)[-1]['text']
        self.assertIn('1. Правое отделение: предусмотрены двери', text)
        self.assertIn('2. Правое отделение: что находится внутри', text)
        self.assertIn('3. Укажите ширину', text)
        self.assertIn('Перед созданием эскиза нужно уточнить', text)

    def save(self, number):
        self.store.approve(10, self.pid, number)
        self.store.save_artifact(10, self.pid, number, PNG, 'offline-test')

    def test_sketch_numbers_ignore_edits_and_retry_is_stable(self):
        for _ in range(5):
            self.analyse()
        first = self.store.project(10, self.pid)['revision']
        self.save(first)
        self.assertEqual(self.store.artifact(10, self.pid)['sketch_number'], 1)
        for _ in range(25):
            second = self.analyse()
        self.save(second)
        self.store.save_artifact(10, self.pid, first, PNG, 'retry')
        restarted = Store(self.temp.name)
        self.assertEqual([v['sketch_number'] for v in restarted.drawings(10, self.pid)], [2, 1])
        self.assertEqual(restarted.artifact(10, self.pid, first)['sketch_number'], 1)

    def test_late_earlier_revision_does_not_renumber_existing_image(self):
        first = self.analyse()
        self.store.approve(10, self.pid, first)
        second = self.analyse()
        self.save(second)
        self.store.save_artifact(10, self.pid, first, PNG, 'late')
        self.assertEqual(self.store.artifact(10, self.pid, second)['sketch_number'], 1)
        self.assertEqual(self.store.artifact(10, self.pid, first)['sketch_number'], 2)

    def test_legacy_artifacts_migrate_without_changing_files_or_revisions(self):
        for _ in range(4):
            number = self.analyse()
        self.save(number)
        path = Path(self.store.artifact(10, self.pid)['path'])
        with self.store.connect() as con:
            con.execute('DROP INDEX artifact_sketch_number')
            con.execute('ALTER TABLE artifacts DROP COLUMN sketch_number')
        restarted = Store(self.temp.name)
        self.assertEqual(restarted.artifact(10, self.pid)['sketch_number'], 1)
        self.assertEqual(restarted.project(10, self.pid)['revision'], number)
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(Store(self.temp.name).artifact(10, self.pid)['sketch_number'], 1)

    def test_sketch_numbers_are_per_project(self):
        for _ in range(2):
            self.save(self.analyse())
        self.pid = self.store.create(10)['id']
        self.save(self.analyse())
        self.assertEqual(self.store.artifact(10, self.pid)['sketch_number'], 1)


class ConstructionBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixtures.FakeMessage.bots = {}
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.dialogue, self.sketch = fixtures.FakeDialogue(), fixtures.FakeSketch()
        self.app = Application(self.store, self.dialogue, self.sketch)

    async def test_ui_numbers_only_generated_images_and_history_matches(self):
        message = fixtures.FakeMessage('Шкаф', image=True)
        await self.app.receive(message)
        for text in ('Ширина 2300', 'Высота 1900', 'Глубина 600'):
            message = fixtures.FakeMessage(text)
            await self.app.receive(message)
        pid = self.store.session(10)['project_id']
        self.assertNotIn('Версия ', message.bot.answers[-1][0])
        revision = self.store.project(10, pid)['revision']
        await fixtures.callback(self.app, message, f'generate:{pid}:{revision}')
        texts = '\n'.join(text for text, _ in message.bot.answers)
        self.assertIn('Создаю эскиз.', texts)
        self.assertIn('Эскиз №1', texts)
        self.assertNotIn('чертёж версии', texts)
        await fixtures.callback(self.app, message, f'history:{pid}')
        buttons = [b for row in message.bot.answers[-1][1].inline_keyboard for b in row]
        button = next(b for b in buttons if b.text == 'Эскиз №1')
        await fixtures.callback(self.app, message, button.callback_data)
        self.assertIn('Эскиз №1 · сохранённый результат', message.bot.answers[-1][0])

    async def test_ui_blocks_generation_and_preserves_exact_displayed_questions(self):
        async def unclear(*args):
            response = completed()
            response.cabinet_sections = [section(doors='unknown', interior_known=False)]
            response.brief.width_mm = None
            return analysis_result(response, args[1])
        self.dialogue.analyse = unclear
        message = fixtures.FakeMessage('Шкаф', image=True)
        await self.app.receive(message)
        pid = self.store.session(10)['project_id']
        text, markup = message.bot.answers[-1]
        self.assertFalse(any(b.callback_data.startswith('generate:')
                             for row in markup.inline_keyboard for b in row))
        saved = self.store.messages(10, pid)[-1]['text']
        for question in saved.splitlines()[2:]:
            self.assertIn(question, text)
        revision = self.store.project(10, pid)['revision']
        await fixtures.callback(self.app, message, f'generate:{pid}:{revision}')
        self.assertEqual(self.sketch.calls, [])
