import base64
from io import BytesIO
import unittest
from unittest.mock import AsyncMock
from PIL import Image
from contracts.workflow import StagePlan, StageImageRequest, StageAnalysisRequest
from contracts.drawing import Term
from infrastructure.stage_ai import strict_schema, AIError
from services.dimensions.src.provider import DimensionsProvider, checked_plan
from services.dimensions.src.models import DimensionsAnalysis
from services.dimensions.src.geometry import calculate, compile_scene, projection, preserve_structure, GeometryError
from services.dimensions.src.drawing import render_png, MODEL
from tests.drawing_fixture import cabinet, terms, point
from tests.test_workflow import reference


class GeometryTests(unittest.TestCase):
    def test_all_views_use_same_drawer_and_section_boundaries(self):
        scene = compile_scene(cabinet())
        drawers = [b for b in scene.boxes if b.id.startswith('left_drawer')]
        self.assertEqual(len(drawers), 2)
        for drawer in drawers:
            front, top = projection(drawer, 'front'), projection(drawer, 'top')
            self.assertEqual((front[0][0], front[1][0]), (16, 784))
            self.assertEqual((top[0][0], top[1][0]), (16, 784))
        self.assertEqual(len([b for b in scene.boxes if b.kind == 'shelf']), 3)
        self.assertEqual(len([b for b in scene.boxes if b.kind == 'rod']), 3)
        self.assertEqual(len([b for b in scene.boxes if b.kind == 'door']), 1)

    def test_formula_uses_thickness_and_updates_after_dimension_change(self):
        plan = cabinet()
        next(d for d in plan.dimensions if d.id == 'thickness').value_mm = 18
        self.assertEqual(calculate(plan.dimensions)['drawer_width'], 764)
        with self.assertRaisesRegex(GeometryError, 'устарел'):
            compile_scene(plan)
        next(d for d in plan.dimensions if d.id == 'drawer_width').value_mm = 764
        box = next(b for b in compile_scene(plan).boxes if b.id == 'left_drawer_0')
        self.assertEqual(box.high[0] - box.low[0], 764)

    def test_formula_does_not_trust_ai_numeric_answer(self):
        plan = cabinet()
        next(d for d in plan.dimensions if d.id == 'drawer_width').value_mm = 100
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=cabinet(), messages=[], references=[reference()])
        result = checked_plan(plan, request)
        self.assertEqual(next(d.value_mm for d in result.dimensions if d.id == 'drawer_width'), 768)

    def test_unresolved_source_is_not_replaced_with_typical_thickness(self):
        plan = cabinet()
        next(d for d in plan.dimensions if d.id == 'thickness').value_mm = None
        self.assertIsNone(calculate(plan.dimensions)['drawer_width'])
        with self.assertRaisesRegex(GeometryError, 'исходные'):
            compile_scene(plan)

    def test_cycles_and_missing_dependencies_rejected(self):
        for ref in ('drawer_width', 'missing'):
            plan = cabinet()
            plan.dimensions[-1].formula = [Term(dimension=ref, coefficient=1)]
            with self.subTest(ref=ref), self.assertRaises(GeometryError):
                calculate(plan.dimensions)

    def test_negative_internal_clearance_rejected(self):
        plan = cabinet()
        next(d for d in plan.dimensions if d.id == 'thickness').value_mm = 500
        with self.assertRaisesRegex(GeometryError, 'отрицательным'):
            calculate(plan.dimensions)

    def test_wrong_label_and_missing_measure_rejected(self):
        for kind in ('label', 'missing', 'axis'):
            plan = cabinet()
            if kind == 'label':
                plan.drawing.measures[0].end.x = [Term(dimension='left_boundary', coefficient=1)]
            elif kind == 'missing':
                plan.drawing.measures.pop()
            else:
                plan.drawing.measures[0].axis = 'y'
            with self.subTest(kind=kind), self.assertRaises(GeometryError):
                compile_scene(plan)

    def test_outside_and_intersecting_parts_rejected(self):
        for kind in ('outside', 'overlap'):
            plan = cabinet()
            if kind == 'outside':
                plan.drawing.parts[0].high.y = [Term(dimension='overall_height', coefficient=2)]
            else:
                part = plan.drawing.parts[0].model_copy(deep=True)
                part.id = 'duplicate_wall'
                plan.drawing.parts.append(part)
            with self.subTest(kind=kind), self.assertRaises(GeometryError):
                compile_scene(plan)

    def test_door_taller_than_body_rejected(self):
        plan = cabinet()
        door = next(b for b in plan.drawing.parts if b.kind == 'door')
        door.high.y.append(Term(dimension='drawer_height', coefficient=1))
        with self.assertRaisesRegex(GeometryError, 'габариты'):
            compile_scene(plan)

    def test_locked_scene_cannot_narrow_drawers_or_remove_shelf(self):
        for kind in ('width', 'remove', 'formula', 'null'):
            previous = cabinet()
            changed = previous.model_copy(deep=True)
            if kind == 'width':
                drawer = next(p for p in changed.drawing.parts if p.kind == 'drawer_front')
                drawer.high.x = [Term(dimension='left_boundary', coefficient=0.5)]
            elif kind == 'remove':
                changed.drawing.parts.pop()
            elif kind == 'formula':
                changed.dimensions[-1].formula = [Term(dimension='left_boundary', coefficient=0.5)]
            else:
                changed.drawing = None
            preserve_structure(changed, previous)
            with self.subTest(kind=kind):
                self.assertTrue(changed.needs_sketch_changes)
                self.assertEqual(changed.drawing, previous.drawing)
                self.assertEqual(changed.dimensions[-1].formula, previous.dimensions[-1].formula)

    def test_number_changes_keep_structure(self):
        previous = cabinet()
        current = previous.model_copy(deep=True)
        current.dimensions[0].value_mm = 2100
        preserve_structure(current, previous)
        self.assertFalse(current.needs_sketch_changes)
        self.assertEqual(current.drawing, previous.drawing)

    def test_reordering_parts_terms_and_measurements_is_not_a_design_change(self):
        previous = cabinet()
        current = previous.model_copy(deep=True)
        current.drawing.parts.reverse()
        current.drawing.measures.reverse()
        current.dimensions[-1].formula.reverse()
        for part in current.drawing.parts:
            part.high.x.reverse()
        preserve_structure(current, previous)
        self.assertFalse(current.needs_sketch_changes)
        self.assertFalse(current.questions)
        self.assertEqual(current.drawing, previous.drawing)

    def test_unknown_input_referenced_by_geometry_survives_next_analysis(self):
        from services.dimensions.src.evidence import analysis_context
        plan = cabinet()
        next(d for d in plan.dimensions if d.id == 'thickness').value_mm = None
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=plan,
                                       messages=[], references=[reference()])
        context = analysis_context(request)
        self.assertIn('thickness', {d['id'] for d in context['plan']['dimensions']})

    def test_legacy_plan_is_readable_but_cannot_generate_new_ambiguous_image(self):
        raw = cabinet().model_dump(exclude={'drawing', 'drawing_locked'})
        for dimension in raw['dimensions']:
            dimension.pop('formula', None)
        legacy = StagePlan.model_validate(raw)
        self.assertIsNone(legacy.drawing)
        with self.assertRaisesRegex(GeometryError, 'схемы'):
            DimensionsProvider.validate_generation(legacy)

    def test_render_is_repeatable_and_decodes_to_valid_png(self):
        plan = cabinet()
        first = render_png(plan)
        self.assertEqual(first, render_png(plan))
        with Image.open(BytesIO(first)) as image:
            image.load()
            self.assertEqual(image.format, 'PNG')
            self.assertGreater(image.width, 2000)
            self.assertGreater(image.height, 2000)

    def test_schema_is_strict_including_nullable_compatibility_fields(self):
        schema = strict_schema(DimensionsAnalysis)
        def visit(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    self.assertEqual(set(node['required']), set(node['properties']))
                    self.assertFalse(node['additionalProperties'])
                self.assertNotIn('default', node)
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        visit(schema)


class DrawingProviderTests(unittest.IsolatedAsyncioTestCase):
    def request_and_raw(self):
        plan = cabinet()
        raw = DimensionsAnalysis(**plan.model_dump(), removed_dimensions=[])
        request = StageAnalysisRequest(stage='dimensions', brief=plan.brief, plan=None,
            messages=[{'role': 'user', 'text': '\n'.join(d.source_quote for d in plan.dimensions if d.source_quote)}],
            references=[reference()])
        return request, raw

    async def test_bad_coordinates_are_repaired_without_asking_customer_to_fix_model(self):
        request, good = self.request_and_raw()
        bad = good.model_copy(deep=True)
        bad.drawing.parts[0].high.y = [Term(dimension='overall_height', coefficient=2)]
        ai = AsyncMock()
        ai.analyse.side_effect = [bad, good]
        result = await DimensionsProvider(ai).analyse(request)
        self.assertFalse(result.questions)
        self.assertTrue(result.drawing_locked)
        self.assertEqual(ai.analyse.await_count, 2)
        self.assertIn('geometry_feedback', ai.analyse.call_args.args[1])
        ai.image.assert_not_called()

    async def test_bad_geometry_stops_after_one_repair(self):
        request, bad = self.request_and_raw()
        bad.drawing.parts[0].high.y = [Term(dimension='overall_height', coefficient=2)]
        ai = AsyncMock()
        ai.analyse.return_value = bad
        with self.assertRaises(AIError) as caught:
            await DimensionsProvider(ai).analyse(request)
        self.assertEqual(caught.exception.code, 'invalid_geometry')
        self.assertEqual(ai.analyse.await_count, 2)

    async def test_missing_user_data_is_asked_once_without_paid_repair(self):
        request, raw = self.request_and_raw()
        item = next(d for d in raw.dimensions if d.id == 'thickness')
        item.value_mm = None
        item.source_quote = None
        ai = AsyncMock()
        ai.analyse.return_value = raw
        result = await DimensionsProvider(ai).analyse(request)
        self.assertTrue(result.questions)
        self.assertEqual(ai.analyse.await_count, 1)

    async def test_image_generation_never_calls_ai(self):
        ai = AsyncMock()
        provider = DimensionsProvider(ai)
        request = StageImageRequest(stage='dimensions', request_id='geometry-test', project_id='b'*32,
                                    revision=1, plan=cabinet(), references=[reference()])
        result, model = await provider.generate(request)
        self.assertEqual(model, MODEL)
        self.assertTrue(base64.b64decode(result.data_base64).startswith(b'\x89PNG'))
        ai.image.assert_not_called()
        ai.analyse.assert_not_called()

    async def test_geometry_failure_blocks_provider_before_any_image_request(self):
        ai = AsyncMock()
        provider = DimensionsProvider(ai)
        invalid = cabinet()
        invalid.drawing.parts[-1].high.y = [Term(dimension='overall_height', coefficient=2)]
        request = StageImageRequest(stage='dimensions', request_id='bad', project_id='b'*32,
                                    revision=1, plan=invalid, references=[reference()])
        with self.assertRaises(GeometryError):
            await provider.generate(request)
        ai.image.assert_not_called()
