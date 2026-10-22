"""Synthetic, explicitly dimensioned fixtures; no customer records or AI calls."""
from contracts.drawing import Drawing
from contracts.models import Brief
from contracts.workflow import StagePlan, Dimension
from contracts.cabinet import Cabinet


def terms(*items):
    return [{'dimension': name, 'coefficient': coefficient} for name, coefficient in items]


def point(x=None, y=None, z=None):
    return {'x': x or [], 'y': y or [], 'z': z or []}


def span(name):
    return terms((name, 1))


def measurement(ident, axis, view='front', start=None, end=None):
    return {'dimension': ident, 'view': view, 'axis': axis,
            'start': start or point(), 'end': end or point(**{axis: span(ident)})}


def simple_drawing(plan):
    """Small slab for legacy transport fixtures; all supplied dimensions annotated."""
    return Drawing.model_validate({
        'format': 'orthogonal-v1',
        'parts': [{'id': 'sample_shelf', 'label': 'Полка', 'kind': 'shelf',
                   'low': point(), 'high': point(span('shelf_width'), terms(('shelf_width', 0.5)), span('overall_depth'))}],
        'measures': [measurement(d.id, {'width': 'x', 'height': 'y', 'depth': 'z'}[d.axis],
                                'side' if d.axis == 'depth' else 'front') for d in plan.dimensions]})


def cabinet():
    """A declared test cabinet: 2000×1700×600, not a reconstruction of user data."""
    values = {'overall_width': (2000, 'width', 'Ширина шкафа'),
              'overall_height': (1700, 'height', 'Высота шкафа'),
              'overall_depth': (600, 'depth', 'Глубина шкафа'),
              'left_boundary': (800, 'width', 'Правая граница левой секции от левого края'),
              'right_boundary': (1400, 'width', 'Левая граница правой секции от левого края'),
              'thickness': (16, 'thickness', 'Толщина панелей'),
              'drawer_height': (250, 'height', 'Высота фасада левого ящика'),
              'right_drawer_height': (400, 'height', 'Высота фасада правого ящика'),
              'rod_offset': (150, 'offset', 'Отступ верхнего края штанги от верха'),
              'rod_diameter': (20, 'thickness', 'Диаметр штанги'),
              'shelf_gap': (300, 'gap', 'Расстояние между верхними поверхностями полок')}
    dims = [Dimension(id=k, target=label, axis=axis, value_mm=value, source_quote=f'{label} {value} мм')
            for k, (value, axis, label) in values.items()]
    dims.append(Dimension(id='drawer_width', target='Ширина фасадов левых ящиков внутри проёма', axis='width',
                          value_mm=768, source_quote=None,
                          formula=terms(('left_boundary', 1), ('thickness', -2))))
    parts = []
    def add(ident, label, kind, low, high):
        parts.append(dict(id=ident, label=label, kind=kind, low=low, high=high))
    W, H, D, T = (span(n) for n in ('overall_width', 'overall_height', 'overall_depth', 'thickness'))
    add('left_wall', 'Левая стенка', 'panel', point(), point(T, H, D))
    add('right_wall', 'Правая стенка', 'panel', point(terms(('overall_width', 1), ('thickness', -1))), point(W, H, D))
    add('bottom', 'Дно', 'panel', point(T), point(terms(('overall_width', 1), ('thickness', -1)), T, D))
    add('roof', 'Крыша', 'panel', point(T, terms(('overall_height', 1), ('thickness', -1))), point(terms(('overall_width', 1), ('thickness', -1)), H, D))
    for i, boundary in enumerate(('left_boundary', 'right_boundary')):
        add(f'divider_{i}', f'Перегородка {i+1}', 'divider', point(terms((boundary, 1), ('thickness', -1)), T),
            point(span(boundary), terms(('overall_height', 1), ('thickness', -1)), D))
    for i in range(2):
        bottom = terms(('thickness', 1), *((('drawer_height', i),) if i else ()))
        top = terms(('thickness', 1), ('drawer_height', i+1))
        add(f'left_drawer_{i}', f'Левый ящик {i+1}, фасад', 'drawer_front', point(T, bottom),
            point(terms(('left_boundary', 1), ('thickness', -1)), top, T))
    add('right_drawer', 'Правый ящик, фасад', 'drawer_front', point(span('right_boundary'), T),
        point(terms(('overall_width', 1), ('thickness', -1)), terms(('thickness', 1), ('right_drawer_height', 1)), T))
    for i in range(3):
        base = terms(('thickness', 1), ('right_drawer_height', 1), *((('shelf_gap', i),) if i else ()))
        high = base + T
        add(f'right_shelf_{i}', f'Правая полка {i+1}', 'shelf', point(span('right_boundary'), base, T),
            point(terms(('overall_width', 1), ('thickness', -1)), high, D))
    bounds = [(T, terms(('left_boundary', 1), ('thickness', -1))),
              (span('left_boundary'), terms(('right_boundary', 1), ('thickness', -1))),
              (span('right_boundary'), terms(('overall_width', 1), ('thickness', -1)))]
    for i, (lowx, highx) in enumerate(bounds):
        add(f'rod_{i}', f'Штанга секции {i+1}', 'rod',
            point(lowx, terms(('overall_height', 1), ('rod_offset', -1), ('rod_diameter', -1)), terms(('overall_depth', 0.5))),
            point(highx, terms(('overall_height', 1), ('rod_offset', -1)), terms(('overall_depth', 0.5), ('rod_diameter', 1))))
    add('right_door', 'Дверь правой секции', 'door', point(span('right_boundary')),
        point(W, H, T))
    measures = [measurement('overall_width', 'x'), measurement('overall_height', 'y'), measurement('overall_depth', 'z', 'side'),
                measurement('left_boundary', 'x'), measurement('right_boundary', 'x'), measurement('thickness', 'x'),
                measurement('drawer_height', 'y', start=point(T, T), end=point(T, terms(('thickness', 1), ('drawer_height', 1)))),
                measurement('right_drawer_height', 'y', start=point(span('right_boundary'), T), end=point(span('right_boundary'), terms(('thickness', 1), ('right_drawer_height', 1)))),
                measurement('rod_offset', 'y', start=point(T, terms(('overall_height', 1), ('rod_offset', -1))), end=point(T, H)),
                measurement('rod_diameter', 'y', start=point(T, terms(('overall_height', 1), ('rod_offset', -1), ('rod_diameter', -1))), end=point(T, terms(('overall_height', 1), ('rod_offset', -1)))),
                measurement('shelf_gap', 'y', start=point(span('right_boundary'), terms(('thickness', 2), ('right_drawer_height', 1))), end=point(span('right_boundary'), terms(('thickness', 2), ('right_drawer_height', 1), ('shelf_gap', 1)))),
                measurement('drawer_width', 'x', start=point(T, T), end=point(terms(('left_boundary', 1), ('thickness', -1)), T))]
    return StagePlan(brief=Brief(title='Проверочный шкаф', description='Синтетический пример для проверки геометрии.',
                               width_mm=2000, height_mm=1700, depth_mm=600, material='ЛДСП 16 мм', color=None, hardware=None),
                     dimensions=dims, inventory_complete=True, needs_sketch_changes=False, questions=[], reply='Проверка схемы.',
                     drawing=Drawing(format='orthogonal-v1', parts=parts, measures=measures), drawing_locked=True)


def semantic_cabinet():
    plan = cabinet()
    plan.drawing = None
    plan.drawing_locked = False
    for ident, value, axis in [('middle_width', 600, 'width'), ('right_width', 600, 'width'), ('bottom_offset', 20, 'offset')]:
        plan.dimensions.append(Dimension(id=ident, target=ident, axis=axis, value_mm=value, source_quote=f'{ident} {value}'))
    def element(ident, kind, position, height=None, offset=None):
        return dict(id=ident, label=ident, kind=kind, width_mode='opening', width_dimension=None, alignment=None,
                    position_reference=position, offset_dimension=offset, height_dimension=height,
                    depth_dimension='overall_depth' if kind == 'shelf' else None)
    plan.cabinet = Cabinet.model_validate(dict(panel_thickness_dimension='thickness', facade_thickness_dimension='thickness',
        width_basis='partition_centers', joint='between_sides', sections=[
            dict(id='left', label='Левая секция', width_dimension='left_boundary', door=None, elements=[
                element('left_drawer_1', 'drawer_front', 'inner_bottom', 'drawer_height'),
                element('left_drawer_2', 'drawer_front', 'previous_top', 'drawer_height'),
                element('left_rod', 'rod', 'top', offset='rod_offset')]),
            dict(id='middle', label='Средняя секция', width_dimension='middle_width', door=None,
                 elements=[element('middle_rod', 'rod', 'top', offset='rod_offset')]),
            dict(id='right', label='Правая секция', width_dimension='right_width',
                 door=dict(id='right_door', label='Правая дверь', width_dimension='right_width',
                           height_dimension='overall_height', bottom_offset_dimension=None), elements=[
                     element('right_drawer', 'drawer_front', 'floor', 'right_drawer_height', 'bottom_offset'),
                     element('right_shelf_1', 'shelf', 'previous_top'),
                     element('right_shelf_2', 'shelf', 'previous_top', offset='shelf_gap'),
                     element('right_shelf_3', 'shelf', 'previous_top', offset='shelf_gap'),
                     element('right_rod', 'rod', 'top', offset='rod_offset')])]))
    plan.dimensions = [d for d in plan.dimensions if d.id not in ('right_boundary', 'rod_diameter', 'drawer_width')]
    return plan
