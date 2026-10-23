import unittest
from unittest.mock import AsyncMock
from contracts.workflow import Dimension, StageAnalysisRequest
from infrastructure.stage_ai import AIError
from services.dimensions.src.cabinet import build, legacy_unlinked_question
from services.dimensions.src.geometry import compile_scene
from services.dimensions.src.models import DimensionsAnalysis, DimensionAlias, DimensionLinkRepair, ShelfGapLink
from services.dimensions.src.reconciliation import apply_links, merge_aliases
from services.dimensions.src.provider import DimensionsProvider, checked_plan
from tests.drawing_fixture import semantic_cabinet
from tests.test_workflow import reference


def prepared():
    plan = semantic_cabinet()
    gap = next(d for d in plan.dimensions if d.id == 'shelf_gap')
    gap.target = 'Чистый просвет между полками правой секции'
    gap.source_quote = 'В правой секции три полки, чистый просвет между ними 300 мм'
    return plan


def raw(plan, aliases=()):
    return DimensionsAnalysis(**plan.model_dump(), removed_dimensions=[],
                              dimension_aliases=list(aliases))


def request(plan):
    return StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=plan,
        messages=[{'role': 'user', 'text': '\n'.join(d.source_quote for d in plan.dimensions if d.source_quote)}],
        references=[reference()])


class LinkRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlinked_gap_and_stale_question_are_repaired_internally(self):
        good = prepared()
        bad = good.model_copy(deep=True)
        for shelf in bad.cabinet.sections[-1].elements[2:4]:
            shelf.offset_dimension = None
        bad.questions = [legacy_unlinked_question(next(d for d in bad.dimensions if d.id == 'shelf_gap'))]
        req = request(bad)
        before = req.model_dump_json()
        ai = AsyncMock()
        repair = DimensionLinkRepair(shelf_gaps=[ShelfGapLink(section_id='right', dimension_id='shelf_gap',
            shelf_ids=['right_shelf_1', 'right_shelf_2', 'right_shelf_3'], basis='clear_gap',
            source_quote=next(d.source_quote for d in good.dimensions if d.id == 'shelf_gap'))],
            dimension_aliases=[], questions=[])
        ai.analyse.side_effect = [raw(bad), repair]
        result = await DimensionsProvider(ai).analyse(req)
        self.assertFalse(result.questions)
        self.assertEqual(ai.analyse.await_count, 2)
        self.assertIn('shelf_gap', {d['id'] for d in ai.analyse.call_args.args[1]['unlinked_dimensions']})
        scene = compile_scene(result)
        shelves = [b for b in scene.boxes if b.kind == 'shelf']
        self.assertEqual(len(shelves), 3)
        self.assertEqual([shelves[i].low[1] - shelves[i-1].high[1] for i in (1, 2)], [300, 300])
        self.assertEqual(req.model_dump_json(), before)
        ai.image.assert_not_called()
        self.assertEqual(ai.analyse.call_args.args[2], [])
        self.assertIs(ai.analyse.call_args.args[3], DimensionLinkRepair)

    async def test_repeated_broken_links_stop_without_repeating_client_question(self):
        bad = prepared()
        for shelf in bad.cabinet.sections[-1].elements[2:4]:
            shelf.offset_dimension = None
        ai = AsyncMock()
        ai.analyse.return_value = raw(bad)
        with self.assertRaises(AIError):
            await DimensionsProvider(ai).analyse(request(bad))
        self.assertEqual(ai.analyse.await_count, 2)

    async def test_measurement_basis_question_retains_known_number_and_section(self):
        plan = prepared()
        plan.questions = ['300 мм между полками правой секции — чистый просвет или шаг?']
        ai = AsyncMock()
        ai.analyse.return_value = raw(plan)
        result = await DimensionsProvider(ai).analyse(request(plan))
        self.assertEqual(result.questions, plan.questions)
        self.assertEqual(next(d.value_mm for d in result.dimensions if d.id == 'shelf_gap'), 300)
        self.assertEqual(ai.analyse.await_count, 1)


class AliasAndPitchTests(unittest.TestCase):
    def test_unknown_basis_asks_only_about_surfaces_without_losing_number(self):
        plan = prepared()
        for shelf in plan.cabinet.sections[-1].elements[2:4]:
            shelf.offset_dimension = None
        quote = 'В правой части шкафа есть 3 полки, расстояние между ними по 300мм'
        repair = DimensionLinkRepair(shelf_gaps=[dict(section_id='right', dimension_id='shelf_gap',
            shelf_ids=['right_shelf_1', 'right_shelf_2', 'right_shelf_3'], basis='clear_gap', source_quote=quote)],
            dimension_aliases=[], questions=[])
        apply_links(plan, repair, [type('Message', (), {'role':'user', 'text':quote})()])
        self.assertEqual(len(plan.questions), 1)
        self.assertIn('300 мм', plan.questions[0])
        self.assertIn('Правая секция', plan.questions[0])
        self.assertIn('чистый просвет', plan.questions[0])
        self.assertIsNone(plan.cabinet.sections[-1].elements[2].offset_dimension)

    def test_repair_cannot_change_existing_offset_or_include_drawer(self):
        for conflict in ('offset', 'drawer'):
            plan = prepared()
            plan.cabinet.sections[-1].elements[2].offset_dimension = 'bottom_offset'
            ids = ['right_shelf_1', 'right_shelf_2', 'right_shelf_3'] if conflict == 'offset' else ['right_drawer', 'right_shelf_1']
            quote = next(d.source_quote for d in plan.dimensions if d.id == 'shelf_gap')
            repair = DimensionLinkRepair(shelf_gaps=[dict(section_id='right', dimension_id='shelf_gap',
                shelf_ids=ids, basis='clear_gap', source_quote=quote)], dimension_aliases=[], questions=[])
            before = plan.model_dump_json()
            from services.dimensions.src.geometry import GeometryError
            with self.subTest(conflict=conflict), self.assertRaises(GeometryError):
                apply_links(plan, repair, request(plan).messages)
            self.assertEqual(plan.model_dump_json(), before)

    def with_duplicate(self):
        plan = prepared()
        canonical = next(d for d in plan.dimensions if d.id == 'bottom_offset')
        canonical.target = 'Отступ нижнего ящика правой секции от пола'
        canonical.source_quote = '20мм'
        duplicate = Dimension(id='old_drawer_position', target='Положение нижнего ящика правой секции от низа',
                              axis='height', value_mm=20, source_quote='20мм')
        plan.dimensions.append(duplicate)
        alias = DimensionAlias(duplicate_id=duplicate.id, canonical_id=canonical.id, source_quote='20мм')
        return plan, alias

    def test_two_names_for_same_position_merge_without_losing_value(self):
        plan, alias = self.with_duplicate()
        result = checked_plan(raw(plan, [alias]), request(plan))
        self.assertNotIn(alias.duplicate_id, {d.id for d in result.dimensions})
        self.assertEqual(next(d.value_mm for d in result.dimensions if d.id == alias.canonical_id), 20)
        result.drawing, result.dimensions = build(result)
        compile_scene(result)

    def test_legacy_unique_drawer_position_is_deduplicated_without_model_alias(self):
        plan, alias = self.with_duplicate()
        result = checked_plan(raw(plan), request(plan))
        self.assertNotIn(alias.duplicate_id, {d.id for d in result.dimensions})
        self.assertEqual(next(d.value_mm for d in result.dimensions if d.id == alias.canonical_id), 20)

    def test_multiple_drawers_do_not_allow_automatic_position_merge(self):
        plan, alias = self.with_duplicate()
        second = plan.cabinet.sections[-1].elements[0].model_copy(deep=True)
        second.id = 'another_right_drawer'
        plan.cabinet.sections[-1].elements.append(second)
        result = checked_plan(raw(plan), request(plan))
        self.assertIn(alias.duplicate_id, {d.id for d in result.dimensions})

    def test_unsafe_aliases_do_not_discard_measurement(self):
        for change in ('value', 'quote', 'section', 'physical_height', 'already_used', 'overall'):
            plan, alias = self.with_duplicate()
            duplicate = plan.dimensions[-1]
            if change == 'value':
                duplicate.value_mm = 25
                duplicate.source_quote = '25мм'
            elif change == 'quote':
                alias.source_quote = 'подтверждаю 20мм'
            elif change == 'section':
                duplicate.target = duplicate.target.replace('правой', 'левой')
            elif change == 'physical_height':
                duplicate.target = 'Высота фасада нижнего ящика правой секции'
            elif change == 'already_used':
                plan.cabinet.sections[-1].elements[0].offset_dimension = duplicate.id
            else:
                alias.duplicate_id = 'overall_width'
            with self.subTest(change=change):
                result = plan.model_copy(deep=True)
                merge_aliases(result, [alias], request(plan).messages)
                self.assertIn(alias.duplicate_id, {d.id for d in result.dimensions})

    def test_pitch_preserves_300_between_corresponding_faces(self):
        plan = prepared()
        for shelf in plan.cabinet.sections[-1].elements[2:4]:
            shelf.position_reference = 'previous_bottom'
        plan.drawing, plan.dimensions = build(plan)
        scene = compile_scene(plan)
        shelves = [b for b in scene.boxes if b.kind == 'shelf']
        self.assertEqual([shelves[i].high[1] - shelves[i-1].high[1] for i in (1, 2)], [300, 300])
        self.assertEqual([shelves[i].low[1] - shelves[i-1].high[1] for i in (1, 2)], [284, 284])
