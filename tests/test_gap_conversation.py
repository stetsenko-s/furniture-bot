import unittest
from unittest.mock import AsyncMock
from contracts.models import ConversationMessage
from contracts.workflow import StagePlan
from services.dimensions.src.gap_answers import answer_for, explicit_basis, gap_question
from services.dimensions.src.models import DimensionLinkRepair
from services.dimensions.src.reconciliation import apply_links
from services.dimensions.src.provider import DimensionsProvider, checked_plan
from services.dimensions.src.geometry import compile_scene
from tests.test_dimension_links import prepared, request, raw


def pending():
    plan = prepared()
    gap = next(d for d in plan.dimensions if d.id == 'shelf_gap')
    gap.target = 'Расстояние между полками в правой секции'
    gap.source_quote = 'В правой части шкафа есть 3 полки, расстояние между ними по 300мм'
    for shelf in plan.cabinet.sections[-1].elements[2:4]:
        shelf.offset_dimension = None
    plan.questions = [gap_question('Правая секция', 300)]
    return plan


def message(role, text):
    return ConversationMessage(role=role, text=text)


def shown(question):
    return message('assistant', 'Нужно уточнить:\n1. ' + question)


class GapConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_reply_then_typo_then_unrelated_edit_and_reload(self):
        old = pending()
        req = request(old)
        first_shelf = old.cabinet.sections[-1].elements[1].model_dump()
        for answer in ('это чистый просвет', '1. Это чисты просвет', 'Цвет коричневый'):
            req.messages.extend([shown(old.questions[0]), message('user', answer)])
            before = req.model_dump_json()
            ai = AsyncMock()
            # Simulate the observed failure: a new LLM response repeats both
            # the old question and the old, unlinked construction.
            ai.analyse.return_value = raw(old)
            result = await DimensionsProvider(ai).analyse(req)
            self.assertFalse(result.questions)
            self.assertEqual(ai.analyse.await_count, 1)
            self.assertEqual(req.model_dump_json(), before)
            self.assertEqual(result.shelf_gap_bindings[0].basis, 'clear_gap')
            self.assertEqual(next(d.value_mm for d in result.dimensions if d.id == 'shelf_gap'), 300)
            self.assertEqual(result.cabinet.sections[-1].elements[1].model_dump(), first_shelf)
            shelves = [b for b in compile_scene(result).boxes if b.kind == 'shelf']
            self.assertEqual([shelves[i].low[1] - shelves[i-1].high[1] for i in (1, 2)], [300, 300])
            req.plan = StagePlan.model_validate_json(result.model_dump_json())

    async def test_saved_basis_survives_truncated_history_and_new_size(self):
        old = pending()
        req = request(old)
        req.messages.extend([shown(old.questions[0]), message('user', 'Это чистый просвет')])
        confirmed = checked_plan(raw(old), req)
        req.plan = StagePlan.model_validate_json(confirmed.model_dump_json())
        req.messages = [message('user', 'Измени расстояние между полками правой секции на 320 мм')]
        changed = old.model_copy(deep=True)
        gap = next(d for d in changed.dimensions if d.id == 'shelf_gap')
        gap.value_mm, gap.source_quote = 320, req.messages[0].text
        result = checked_plan(raw(changed), req)
        self.assertFalse(result.questions)
        self.assertEqual(result.shelf_gap_bindings[0].basis, 'clear_gap')
        self.assertEqual(next(d.value_mm for d in result.dimensions if d.id == 'shelf_gap'), 320)

    def test_compact_repair_old_quote_does_not_erase_actual_answer(self):
        plan = pending()
        req = request(plan)
        req.messages.extend([shown(plan.questions[0]), message('user', '1. Это чисты просвет')])
        quote = next(d.source_quote for d in plan.dimensions if d.id == 'shelf_gap')
        repair = DimensionLinkRepair(shelf_gaps=[dict(section_id='right', dimension_id='shelf_gap',
            shelf_ids=['right_shelf_1', 'right_shelf_2', 'right_shelf_3'],
            basis=None, source_quote=quote)], dimension_aliases=[], questions=plan.questions)
        apply_links(plan, repair, req.messages)
        self.assertFalse(plan.questions)
        self.assertEqual(plan.shelf_gap_bindings[0].source_quote, 'Это чисты просвет')
        self.assertEqual(plan.cabinet.sections[-1].elements[2].offset_dimension, 'shelf_gap')

    def test_unanswered_binding_is_saved_and_can_be_answered_next_turn(self):
        plan = pending()
        req = request(plan)
        quote = next(d.source_quote for d in plan.dimensions if d.id == 'shelf_gap')
        repair = DimensionLinkRepair(shelf_gaps=[dict(section_id='right', dimension_id='shelf_gap',
            shelf_ids=['right_shelf_1', 'right_shelf_2', 'right_shelf_3'],
            basis=None, source_quote=quote)], dimension_aliases=[], questions=[])
        apply_links(plan, repair, req.messages)
        self.assertIsNone(plan.shelf_gap_bindings[0].basis)
        req.plan = StagePlan.model_validate_json(plan.model_dump_json())
        req.messages.extend([shown(plan.questions[0]), message('user', 'Между верхними поверхностями')])
        result = checked_plan(raw(plan), req)
        self.assertFalse(result.questions)
        self.assertEqual(result.shelf_gap_bindings[0].basis, 'pitch')
        self.assertEqual(result.cabinet.sections[-1].elements[2].position_reference, 'previous_bottom')

    def test_unrelated_question_is_kept(self):
        plan = pending()
        req = request(plan)
        req.messages.extend([shown(plan.questions[0]), message('user', 'это чистый просвет')])
        candidate = raw(plan)
        candidate.questions.append('Какова глубина полок?')
        result = checked_plan(candidate, req)
        self.assertEqual(result.questions, ['Какова глубина полок?'])

    def test_new_choice_overrides_previous_before_approval(self):
        plan = pending()
        req = request(plan)
        req.messages.extend([shown(plan.questions[0]), message('user', 'это чистый просвет')])
        req.plan = checked_plan(raw(plan), req)
        req.messages.extend([shown(plan.questions[0]), message('user', 'Между верхними поверхностями')])
        result = checked_plan(raw(plan), req)
        self.assertEqual(result.shelf_gap_bindings[0].basis, 'pitch')

    def test_missing_binding_cannot_attach_to_different_parts(self):
        plan = pending()
        req = request(plan)
        req.messages.extend([shown(plan.questions[0]), message('user', 'это чистый просвет')])
        changed = raw(plan)
        changed.cabinet.sections[-1].elements[2].kind = 'drawer_front'
        result = checked_plan(changed, req)
        self.assertTrue(result.questions)
        self.assertEqual(result.shelf_gap_bindings, [])


class AnswerContextTests(unittest.TestCase):
    def test_sections_and_numbered_answers_are_distinct(self):
        history = [message('assistant', 'Нужно уточнить:\n1. ' + gap_question('Правая секция', 300)
                           + '\n2. ' + gap_question('Левая секция', 300)),
                   message('user', '1. чистый просвет\n2. между верхними поверхностями')]
        self.assertEqual(answer_for('Правая секция', history)[0], 'clear_gap')
        self.assertEqual(answer_for('Левая секция', history)[0], 'pitch')

    def test_unscoped_reply_to_multiple_questions_is_not_guessed(self):
        history = [message('assistant', 'Нужно уточнить:\n1. ' + gap_question('Правая секция', 300)
                           + '\n2. ' + gap_question('Левая секция', 300)),
                   message('user', 'чистый просвет')]
        self.assertIsNone(answer_for('Правая секция', history))
        self.assertIsNone(answer_for('Левая секция', history))

    def test_question_denial_and_other_context_do_not_confirm_basis(self):
        for answer in ('что такое чистый просвет?', 'не чистый просвет', 'чистый просвет или шаг', 'не знаю'):
            self.assertIsNone(explicit_basis(answer))
        history = [shown(gap_question('Правая секция', 300)),
                   message('assistant', 'Нужно уточнить:\n1. Какой цвет?'),
                   message('user', 'чистый просвет')]
        self.assertIsNone(answer_for('Правая секция', history))
