"""Регрессии: отсутствующее отделение, отступ 150 мм, нумерованные ответы."""
import unittest
from contracts.models import ConversationMessage
from services.dimensions.src.models import DimensionsAnalysis
from services.dimensions.src.evidence import numbered_answers, numeric_quote
from services.dimensions.src.provider import checked_plan, DimensionsProvider
from tests.test_workflow import plan, analysis
from tests import test_workflow as fixtures


def raw(value, removals=()):
    return DimensionsAnalysis(**value.model_dump(), removed_dimensions=list(removals))


class DimensionCorrectionTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        previous = plan()
        previous.drawing = None  # Saved pre-geometry project; numbers are still editable.
        item = previous.dimensions[-1]
        item.id = 'top_compartment'
        item.target = 'высота верхнего отделения левой секции'
        item.axis = 'height'
        item.value_mm = None
        item.source_quote = None
        previous.questions = [f'Укажите в миллиметрах размер «{item.target}» (высота).']
        return previous

    async def test_explicit_denial_removes_unknown_item_and_stale_question(self):
        previous = self.fixture()
        request = analysis(previous=previous, text='1. Нет верхнего отделения, там только полка')
        value = previous.model_copy(deep=True)
        value.dimensions = value.dimensions[:3]
        result = checked_plan(raw(value, [{'id': 'top_compartment', 'source_quote': 'Нет верхнего отделения, там только полка'}]), request)
        self.assertNotIn('top_compartment', {d.id for d in result.dimensions})
        self.assertEqual(result.questions, [])
        with self.assertRaisesRegex(ValueError, 'схемы'):
            DimensionsProvider.validate_generation(result)
        # Следующий ход не возвращает исчезнувший пункт из предыдущего плана.
        again = checked_plan(raw(result), analysis(previous=result, text='Далее'))
        self.assertEqual(again.dimensions, result.dimensions)

    async def test_denied_item_cannot_survive_as_null_in_raw_response(self):
        previous = self.fixture()
        result = checked_plan(raw(previous, [{'id': 'top_compartment', 'source_quote': 'Нет отделения'}]),
                              analysis(previous=previous, text='Нет отделения'))
        self.assertFalse(result.questions)
        self.assertEqual(len(result.dimensions), 3)

    async def test_silent_omission_or_fake_denial_does_not_erase_dimensions(self):
        previous = self.fixture()
        previous.dimensions[-1].value_mm = 300
        value = previous.model_copy(deep=True)
        value.dimensions = value.dimensions[:3]
        for removals in ([], [{'id': 'top_compartment', 'source_quote': 'Нет отделения'}],
                         [{'id': 'top_compartment', 'source_quote': '150 мм'}]):
            with self.subTest(removals=removals):
                result = checked_plan(raw(value, removals), analysis(previous=previous, text='150 мм'))
                self.assertIn('top_compartment', {d.id for d in result.dimensions})
                self.assertEqual(bool(result.questions), bool(removals))

    async def test_overall_cannot_be_removed_by_internal_correction(self):
        previous = plan()
        value = raw(previous, [{'id': 'overall_width', 'source_quote': 'Удали ширину'}])
        result = checked_plan(value, analysis(previous=previous, text='Удали ширину'))
        self.assertTrue(any(d.id == 'overall_width' for d in result.dimensions))

    async def test_150_from_top_stays_offset_with_new_target_and_no_repeated_question(self):
        previous = self.fixture()
        previous.dimensions[-1].id = 'left_rod'
        previous.dimensions[-1].target = 'высота вешалки в левой секции'
        previous.questions = ['Укажите в миллиметрах размер «высота вешалки в левой секции» (высота).']
        value = previous.model_copy(deep=True)
        value.dimensions[-1].axis = 'offset'
        value.dimensions[-1].target = 'Отступ штанги левой секции от верхнего края корпуса'
        value.dimensions[-1].value_mm = 150
        value.dimensions[-1].source_quote = '150 мм от верха'
        request = analysis(previous=previous, text='3. 150мм от верха')
        result = checked_plan(raw(value), request)
        self.assertEqual(result.dimensions[-1].value_mm, 150)
        self.assertEqual(result.dimensions[-1].axis, 'offset')
        self.assertEqual(result.questions, [])
        with self.assertRaisesRegex(ValueError, 'схемы'):
            DimensionsProvider.validate_generation(result)

    async def test_invented_calculation_is_not_accepted_as_user_quote(self):
        value = plan()
        value.dimensions[-1].value_mm = 1550
        value.dimensions[-1].source_quote = 'вешалка ниже верха на 150мм, 1700-150=1550'
        value.reply = 'Все размеры собраны и подтверждены.'
        result = checked_plan(raw(value), analysis(text='вешалка ниже верха на 150мм'))
        self.assertIsNone(result.dimensions[-1].value_mm)
        self.assertTrue(result.questions)
        self.assertNotIn('Все размеры собраны', result.reply)

    async def test_unit_spacing_and_decimals_are_normalized_without_unit_conversion(self):
        for text, quote, value in [('150мм', '150 мм', 150),
                                   ('150,5 мм', '150.5мм', 150.5),
                                   ('150 от верха', '150 от верха', 150)]:
            self.assertTrue(numeric_quote(value, quote, analysis(text=text).messages))
        self.assertFalse(numeric_quote(150, '150 мм', analysis(text='15 см').messages))
        self.assertFalse(numeric_quote(1550, '150мм от верха', analysis(text='150мм от верха').messages))

    async def test_confirmed_value_is_preserved_when_model_accidentally_returns_null(self):
        previous = plan()
        value = previous.model_copy(deep=True)
        value.dimensions[-1].value_mm = None
        result = checked_plan(raw(value), analysis(previous=previous, text='Далее'))
        self.assertEqual(result.dimensions[-1].value_mm, 400)
        self.assertFalse(result.questions)

    async def test_numbered_answers_follow_displayed_questions_not_dimension_order(self):
        messages = [
            ConversationMessage(role='assistant', text='Нужно уточнить:\n1. Верхнее отделение?\n2. Отступ штанги слева?\n3. Отступ штанги в центре?'),
            ConversationMessage(role='user', text='1. Нет верхнего отделения\n2. указал, 150мм\n3. 150мм от верха'),
            ConversationMessage(role='assistant', text='Нужно уточнить:\n1. Отступ ящика?'),
            ConversationMessage(role='user', text='1. 20мм'),
        ]
        pairs = numbered_answers(messages)
        self.assertEqual(pairs[1], {'question': 'Отступ штанги слева?', 'answer_quote': 'указал, 150мм'})
        self.assertEqual(pairs[-1]['question'], 'Отступ ящика?')
        self.assertEqual(pairs[-1]['answer_quote'], '20мм')

    async def test_provider_uses_internal_schema_and_exposes_only_public_plan(self):
        observed = {}
        previous = self.fixture()
        result = raw(previous, [{'id': 'top_compartment', 'source_quote': 'Нет отделения'}])
        class AI:
            async def analyse(self, instructions, data, references, schema):
                observed.update(schema=schema, data=data)
                return result
        answer = await DimensionsProvider(AI()).analyse(analysis(previous=previous, text='Нет отделения'))
        from services.dimensions.src.models import CabinetAnalysis
        self.assertIs(observed['schema'], CabinetAnalysis)
        self.assertIn('numbered_answers', observed['data'])
        self.assertNotIn('removed_dimensions', answer.model_dump())


class RetryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkflowTests.asyncSetUp
    click = fixtures.WorkflowTests.click
    dimensions = fixtures.WorkflowTests.dimensions

    async def test_reanalysis_uses_saved_answers_without_new_user_input(self):
        await self.dimensions()
        v = self.db.version(10, self.pid)
        previous_messages = self.db.messages(10, self.pid, v)
        self.assertTrue(v['analysed'])
        original = self.providers['dimensions'].analyse
        calls = []
        async def record(request):
            calls.append(request)
            return await original(request)
        self.providers['dimensions'].analyse = record
        await self.app.workflow.retry(self.message, 10, self.pid)
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.db.version(10, self.pid)['analysed'])
        self.assertEqual([m for m in self.db.messages(10, self.pid, v) if m['role']=='user'],
                         [m for m in previous_messages if m['role']=='user'])
        await self.click('wfgen', 'd', v['number'])
        self.assertFalse(self.db.prepare_reanalysis(10, self.pid))
