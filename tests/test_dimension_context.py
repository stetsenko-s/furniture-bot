"""Загрязнённый прежними догадками контекст реального диалога."""
import unittest
from contracts.models import ConversationMessage
from contracts.workflow import Dimension
from services.dimensions.src.evidence import analysis_context, rejected_dimensions
from services.dimensions.src.provider import checked_plan
from tests.test_workflow import plan, analysis
from tests.test_dimension_corrections import raw


class ContextTests(unittest.TestCase):
    def request(self):
        previous = plan()
        previous.dimensions.extend([
            Dimension(id='compartment', target='высота верхнего отделения левой секции', axis='height', value_mm=None, source_quote='Нет верхнего отделения'),
            Dimension(id='rod_floor', target='высота штанги от низа шкафа', axis='height', value_mm=None, source_quote='1700-150=1550'),
            Dimension(id='rod_top', target='Отступ штанги от верхнего края корпуса', axis='offset', value_mm=150, source_quote='150мм от верха'),
            Dimension(id='drawer_count', target='число выдвижных ящиков', axis='gap', value_mm=2, source_quote='два ящика'),
        ])
        previous.brief.description = 'Ложное старое описание: отделение существует, высота штанги 1550.'
        previous.reply = 'Все размеры известны, 1550.'
        previous.questions = ['Укажите размер «высота верхнего отделения левой секции» в миллиметрах.']
        request = analysis(previous=previous)
        request.messages += [
            ConversationMessage(role='assistant', text='Выдуманный расчёт 1550.\nНужно уточнить:\n1. Укажите размер «высота верхнего отделения левой секции» в миллиметрах.\n2. Отступ штанги?'),
            ConversationMessage(role='user', text='1. Нет верхнего отделения\n2. 150мм от верха'),
        ]
        return request

    def test_context_keeps_user_answers_and_numbers_but_not_old_guesses(self):
        request = self.request()
        before = request.model_dump_json()
        data = analysis_context(request)
        ids = {d['id'] for d in data['plan']['dimensions']}
        self.assertIn('rod_top', ids)
        self.assertIn('shelf_width', ids)
        self.assertFalse({'rod_floor', 'compartment', 'drawer_count'} & ids)
        self.assertNotIn('1550', str(data))
        self.assertIn('Нет верхнего отделения', str(data['rejected_elements']))
        self.assertEqual(request.model_dump_json(), before)

    def test_user_denial_is_applied_even_when_model_returns_no_removal(self):
        request = self.request()
        candidate = request.plan.model_copy(deep=True)
        candidate.dimensions = [d for d in candidate.dimensions if d.id != 'rod_floor']
        result = checked_plan(raw(candidate), request)
        ids = {d.id for d in result.dimensions}
        self.assertFalse({'compartment', 'rod_floor', 'drawer_count'} & ids)
        self.assertIn('rod_top', ids)
        self.assertFalse(result.questions)

    def test_wrong_answer_number_cannot_remove_width_when_only_compartment_denied(self):
        request = self.request()
        request.plan.dimensions.append(Dimension(id='section_width', target='ширина левой секции', axis='width', value_mm=800, source_quote='ширина 800'))
        request.messages += [
            ConversationMessage(role='assistant', text='Нужно уточнить:\n1. Укажите ширина левой секции'),
            ConversationMessage(role='user', text='1. В левой секции нет верхнего отделения'),
        ]
        self.assertNotIn('section_width', rejected_dimensions(request))
        candidate = plan()
        result = checked_plan(raw(candidate), request)
        self.assertTrue(any(d.id == 'section_width' and d.value_mm == 800 for d in result.dimensions))

    def test_unknown_old_dimensions_are_not_restored_but_confirmed_values_are(self):
        request = self.request()
        result = checked_plan(raw(plan()), request)
        self.assertFalse(any(d.id == 'rod_floor' for d in result.dimensions))
        self.assertTrue(any(d.id == 'rod_top' and d.value_mm == 150 for d in result.dimensions))

    def test_equivalent_confirmed_dimension_with_new_id_is_not_duplicated(self):
        request = analysis(previous=plan())
        candidate = plan()
        candidate.dimensions[-1].id = 'new_shelf_width'
        result = checked_plan(raw(candidate), request)
        self.assertEqual(sum(d.target == 'Полка слева' for d in result.dimensions), 1)

    def test_no_measurement_is_not_a_denial_of_element(self):
        request = self.request()
        request.messages[-1] = ConversationMessage(role='user', text='1. Нет размеров отделения')
        self.assertNotIn('compartment', rejected_dimensions(request))
