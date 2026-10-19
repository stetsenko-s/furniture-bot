import unittest

from bot.construction import clarification_questions
from contracts.models import Clarification, DialogueResult
from tests.test_construction import section
from tests.test_project import completed


def question(text, topic='doors', index=0):
    return Clarification(text=text, topic=topic, section_index=index)


class QuestionMergeTests(unittest.TestCase):
    def test_screenshot_two_sections_and_rephrased_door_question_yield_two_questions(self):
        result = completed()
        result.cabinet_sections = [section(name='Левая секция с полками', doors='unknown'),
                                   section(name='Правая секция со штангой', doors='unknown')]
        result.questions = [question('Есть ли двери у левой секции с полками? '
                                     'Если да, какого типа и сколько?')]
        # Проходим тот же JSON-контракт, что и реальный ответ внешней модели.
        parsed = DialogueResult.model_validate_json(result.model_dump_json())
        merged = clarification_questions(parsed, [])
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0].startswith('Левая секция с полками:'))
        self.assertTrue(merged[1].startswith('Правая секция со штангой:'))
        for text in merged:
            self.assertIn('сколько', text)
            self.assertIn('распашные или раздвижные', text)

    def test_hardware_contradiction_kept_even_when_door_question_is_covered(self):
        result = completed()
        result.cabinet_sections = [section(doors='unknown')]
        conflict = 'Куда будут крепиться ручки, если ранее вы указали, что дверей нет?'
        result.questions = [question('Будут ли здесь двери?'), question(conflict, 'contradiction')]
        merged = clarification_questions(result, [])
        self.assertEqual(len(merged), 2)
        self.assertIn(conflict, merged)

    def test_confirmed_no_doors_and_handles_keeps_only_hardware_question(self):
        result = completed()
        result.cabinet_sections = [section(doors='absent', no_doors_user_quote='Дверей нет')]
        result.questions = [question('Куда установить ручки? Дверей и ящиков нет.', 'contradiction')]
        merged = clarification_questions(result, [{'role': 'user', 'text': 'Дверей нет. Хочу ручки.'}])
        self.assertEqual(merged, [result.questions[0].text])

    def test_handles_can_belong_to_drawer_fronts(self):
        result = completed()
        result.cabinet_sections = [section(doors='absent', no_doors_user_quote='Без дверей')]
        text = 'Ручки нужны на обоих фасадах ящиков?'
        result.questions = [question(text, 'other')]
        self.assertEqual(clarification_questions(result, [{'role': 'user', 'text': 'Без дверей'}]), [text])

    def test_no_cross_section_suppression(self):
        result = completed()
        result.cabinet_sections = [section(name='Слева', doors='unknown'), section(name='Справа')]
        result.questions = [question('Сколько дверей справа?', index=1)]
        merged = clarification_questions(result, [])
        self.assertEqual(len(merged), 2)
        self.assertIn('Сколько дверей справа?', merged)

    def test_unmapped_question_is_not_silently_discarded(self):
        result = completed()
        result.cabinet_sections = [section(doors='unknown')]
        for index in (None, 15):
            with self.subTest(index=index):
                result.questions = [question('Где находится общая дверь?', index=index)]
                self.assertIn(result.questions[0].text, clarification_questions(result, []))

    def test_interior_duplicates_merge_but_dimensions_and_mechanisms_remain(self):
        result = completed()
        result.cabinet_sections = [section(interior_known=False)]
        result.questions = [question('Что за дверью?', 'interior'),
                            question('300 × 5 — расстояния между полками?', 'other'),
                            question('В какую сторону сдвигается дверь?', 'other')]
        merged = clarification_questions(result, [])
        self.assertEqual(len(merged), 3)
        self.assertNotIn('Что за дверью?', merged)
        self.assertIn(result.questions[1].text, merged)
        self.assertIn(result.questions[2].text, merged)

    def test_other_furniture_retains_questions(self):
        result = completed()
        result.is_cabinet = False
        result.cabinet_sections = []
        result.questions = [question('Какая форма столешницы?', 'other', None)]
        self.assertEqual(clarification_questions(result, []), ['Какая форма столешницы?'])
