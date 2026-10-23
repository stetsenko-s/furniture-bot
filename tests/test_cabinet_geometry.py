import unittest
from unittest.mock import AsyncMock
from tests.drawing_fixture import semantic_cabinet
from tests.test_workflow import reference
from contracts.workflow import StageAnalysisRequest
from services.dimensions.src.models import DimensionsAnalysis
from services.dimensions.src.provider import DimensionsProvider
from services.dimensions.src.cabinet import build, MissingConstruction, UnlinkedDimensions
from services.dimensions.src.geometry import compile_scene
from services.dimensions.src.evidence import analysis_context
from services.dimensions.src.models import CabinetAnalysis
from infrastructure.stage_ai import strict_schema


class CabinetGeometryTests(unittest.TestCase):
    def test_confirmed_measurement_cannot_be_silently_dropped_from_drawing(self):
        from contracts.workflow import Dimension
        plan = semantic_cabinet()
        plan.dimensions.append(Dimension(id='unused_gap', target='Расстояние между полками',
                                        axis='gap', value_mm=300, source_quote='между полками 300 мм'))
        with self.assertRaisesRegex(UnlinkedDimensions, '300 мм'):
            build(plan)

    def test_wire_schema_cannot_request_model_coordinates_or_formulas(self):
        schema = strict_schema(CabinetAnalysis)
        self.assertNotIn('drawing', schema['properties'])
        self.assertNotIn('Drawing', schema['$defs'])
        self.assertNotIn('Term', schema['$defs'])
        self.assertNotIn('formula', schema['$defs']['SourceDimension']['properties'])

    def test_pending_semantic_measurements_survive_context_without_coordinates(self):
        plan = semantic_cabinet()
        next(d for d in plan.dimensions if d.id == 'thickness').value_mm = None
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=plan,
                                       messages=[], references=[reference()])
        data = analysis_context(request)
        self.assertIn('thickness', {d['id'] for d in data['plan']['dimensions']})
        self.assertIsNone(data['plan']['drawing'])
        self.assertTrue(all(d['formula'] is None for d in data['plan']['dimensions']))

    def test_semantics_generate_shared_geometry_without_model_coordinates(self):
        plan = semantic_cabinet()
        plan.drawing, plan.dimensions = build(plan)
        scene = compile_scene(plan)
        self.assertEqual(len(scene.boxes), 16)
        drawers = [p for p in scene.boxes if p.id.startswith('left_drawer')]
        self.assertEqual([(d.low[0], d.high[0]) for d in drawers], [(16, 792), (16, 792)])
        self.assertEqual([(d.low[1], d.high[1]) for d in drawers], [(16, 266), (266, 516)])
        self.assertEqual(len([p for p in scene.boxes if p.kind == 'shelf']), 3)
        self.assertEqual(len([p for p in scene.boxes if p.kind == 'rod']), 3)
        self.assertEqual(next(d.value_mm for d in plan.dimensions if d.id == 'calc_left_drawer_1_w'), 776)

    def test_unknown_basis_joint_or_thickness_is_asked_not_guessed(self):
        for field in ('width_basis', 'joint', 'panel_thickness_dimension', 'facade_thickness_dimension'):
            plan = semantic_cabinet()
            setattr(plan.cabinet, field, None)
            with self.subTest(field=field), self.assertRaises(MissingConstruction):
                build(plan)

    def test_clear_opening_widths_must_include_material_in_total(self):
        plan = semantic_cabinet()
        plan.cabinet.width_basis = 'clear_openings'
        with self.assertRaisesRegex(MissingConstruction, '2064'):
            build(plan)

    def test_narrow_shelf_requires_alignment(self):
        plan = semantic_cabinet()
        shelf = plan.cabinet.sections[-1].elements[1]
        shelf.width_mode = 'dimension'
        shelf.width_dimension = 'drawer_height'  # explicit smaller test width
        with self.assertRaisesRegex(MissingConstruction, 'слева, справа или по центру'):
            build(plan)

    def test_actual_height_conflict_is_reported_without_rewriting_measurement(self):
        plan = semantic_cabinet()
        next(d for d in plan.dimensions if d.id == 'right_drawer_height').value_mm = 1800
        with self.assertRaisesRegex(MissingConstruction, 'не помещается'):
            build(plan)
        self.assertEqual(next(d.value_mm for d in plan.dimensions if d.id == 'right_drawer_height'), 1800)


class SemanticProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreferenced_unknown_size_does_not_repeat_irrelevant_question(self):
        from contracts.workflow import Dimension
        plan = semantic_cabinet()
        plan.cabinet.panel_thickness_dimension = None
        plan.dimensions.append(Dimension(id='unused_depth', target='Глубина средней секции',
                                        axis='depth', value_mm=None, source_quote=None))
        raw = DimensionsAnalysis(**plan.model_dump(), removed_dimensions=[])
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=None,
            messages=[{'role': 'user', 'text': '\n'.join(d.source_quote for d in plan.dimensions if d.source_quote)}], references=[reference()])
        ai = AsyncMock()
        ai.analyse.return_value = raw
        result = await DimensionsProvider(ai).analyse(request)
        self.assertNotIn('unused_depth', {d.id for d in result.dimensions})
        self.assertIn('толщина', result.questions[0])
        self.assertEqual(ai.analyse.await_count, 1)

    async def test_number_edit_recomputes_locked_geometry_without_changing_structure(self):
        plan = semantic_cabinet()
        plan.drawing, plan.dimensions = build(plan)
        plan.drawing_locked = True
        candidate = plan.model_copy(deep=True)
        candidate.drawing = None
        candidate.drawing_locked = False
        candidate.dimensions = [d for d in candidate.dimensions if d.formula is None]
        thickness = next(d for d in candidate.dimensions if d.id == 'thickness')
        thickness.value_mm, thickness.source_quote = 18, 'Толщина всех панелей и фасадов 18 мм'
        raw = DimensionsAnalysis(**candidate.model_dump(), removed_dimensions=[])
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=plan,
            messages=[{'role': 'user', 'text': thickness.source_quote}], references=[reference()])
        ai = AsyncMock()
        ai.analyse.return_value = raw
        result = await DimensionsProvider(ai).analyse(request)
        self.assertFalse(result.questions)
        self.assertTrue(result.drawing_locked)
        self.assertEqual(result.cabinet, plan.cabinet)
        scene = compile_scene(result)
        drawer = next(p for p in scene.boxes if p.id == 'left_drawer_1')
        self.assertEqual((drawer.low[0], drawer.high[0]), (18, 791))

    async def test_pending_reply_preserves_locked_program_drawing(self):
        plan = semantic_cabinet()
        plan.drawing, plan.dimensions = build(plan)
        plan.drawing_locked = True
        candidate = plan.model_copy(deep=True)
        candidate.drawing = None
        candidate.drawing_locked = False
        candidate.dimensions = [d for d in candidate.dimensions if d.formula is None]
        candidate.questions = ['Какой материал использовать?']
        raw = DimensionsAnalysis(**candidate.model_dump(), removed_dimensions=[])
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=plan,
                                       messages=[], references=[reference()])
        ai = AsyncMock()
        ai.analyse.return_value = raw
        result = await DimensionsProvider(ai).analyse(request)
        self.assertTrue(result.questions)
        self.assertEqual(result.drawing, plan.drawing)
        self.assertTrue(result.drawing_locked)

    async def test_model_only_supplies_semantics_and_program_computes_coordinates(self):
        plan = semantic_cabinet()
        raw = DimensionsAnalysis(**plan.model_dump(), removed_dimensions=[])
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=None,
            messages=[{'role': 'user', 'text': '\n'.join(d.source_quote for d in plan.dimensions if d.source_quote)}], references=[reference()])
        ai = AsyncMock()
        ai.analyse.return_value = raw
        result = await DimensionsProvider(ai).analyse(request)
        self.assertFalse(result.questions)
        self.assertTrue(result.drawing_locked)
        compile_scene(result)
        self.assertEqual(ai.analyse.await_count, 1)
        ai.image.assert_not_called()

    async def test_missing_thickness_returns_real_question_and_no_coordinate_repair(self):
        plan = semantic_cabinet()
        plan.cabinet.panel_thickness_dimension = None
        raw = DimensionsAnalysis(**plan.model_dump(), removed_dimensions=[])
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=None,
            messages=[{'role': 'user', 'text': '\n'.join(d.source_quote for d in plan.dimensions if d.source_quote)}], references=[reference()])
        ai = AsyncMock()
        ai.analyse.return_value = raw
        result = await DimensionsProvider(ai).analyse(request)
        self.assertIn('толщина', result.questions[0])
        self.assertEqual(ai.analyse.await_count, 1)
