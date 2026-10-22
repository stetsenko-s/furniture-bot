"""Compile semantic sections into orthogonal geometry and derived dimensions.

The cabinet template has side walls, roof and bottom. It does not design hardware,
drawer boxes or a back panel. Rods are schematic lines in the front plane only.
"""
from math import isclose
from contracts.drawing import Drawing, Term
from contracts.workflow import Dimension
from .geometry import GeometryError, calculate, expression, TOLERANCE


class MissingConstruction(GeometryError):
    pass


def construction_key(cabinet):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k != 'label'}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return clean(cabinet.model_dump()) if cabinet else None


def dimension_references(cabinet):
    result = {'overall_width', 'overall_height', 'overall_depth'}
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith('_dimension') and isinstance(child, str):
                    result.add(child)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    if cabinet:
        visit(cabinet.model_dump())
    return result


def ref(ident, coefficient=1):
    return [Term(dimension=ident, coefficient=coefficient)]


def combine(*expressions):
    totals = {}
    for expr in expressions:
        for t in expr:
            totals[t.dimension] = totals.get(t.dimension, 0) + t.coefficient
    return [Term(dimension=k, coefficient=v) for k, v in totals.items() if v]


def neg(expr):
    return [Term(dimension=t.dimension, coefficient=-t.coefficient) for t in expr]


def half(expr):
    return [Term(dimension=t.dimension, coefficient=t.coefficient / 2) for t in expr]


def pt(x=None, y=None, z=None):
    return dict(x=x or [], y=y or [], z=z or [])


def build(plan):
    cabinet = plan.cabinet
    if cabinet is None:
        raise MissingConstruction('Не определена схема секций шкафа.')
    values = calculate(plan.dimensions)
    dims = {d.id: d for d in plan.dimensions}
    parts, measures, generated = [], [], []
    used, part_ids = set(), set()

    def require(ident, question):
        if not ident or ident not in dims or values.get(ident) is None:
            raise MissingConstruction(question)
        used.add(ident)
        return ref(ident)

    def measure(ident, axis, start, end, view='front'):
        measures.append(dict(dimension=ident, view=view, axis=axis, start=start, end=end))
        used.add(ident)

    def derived(ident, target, axis, formula, start, end, view='front'):
        value = expression(formula, values)
        if value <= 0:
            raise MissingConstruction('Размер «' + target + '» не помещается. Проверьте ширины секций и толщину материала.')
        generated.append(Dimension(id=ident, target=target, axis=axis, value_mm=value,
                                   source_quote=None, formula=formula))
        measure(ident, {'width': 'x', 'height': 'y', 'depth': 'z'}[axis], start, end, view)

    def part(ident, label, kind, low, high):
        if ident in part_ids:
            raise GeometryError('Повторяется идентификатор детали: ' + ident)
        part_ids.add(ident)
        parts.append(dict(id=ident, label=label, kind=kind, low=low, high=high))

    W = require('overall_width', 'Какова ширина шкафа?')
    H = require('overall_height', 'Какова высота шкафа?')
    D = require('overall_depth', 'Какова глубина шкафа?')
    T = require(cabinet.panel_thickness_dimension, 'Какова толщина стенок, перегородок и полок в миллиметрах?')
    has_facades = any(s.door or any(e.kind == 'drawer_front' for e in s.elements) for s in cabinet.sections)
    F = require(cabinet.facade_thickness_dimension, 'Какова толщина дверей и фасадов ящиков в миллиметрах?') if has_facades else []
    if cabinet.width_basis is None:
        raise MissingConstruction('Ширины секций указаны как свободные проёмы между стенками или с учётом стенок — до середины перегородок?')
    if cabinet.joint is None:
        raise MissingConstruction('Крыша и дно находятся между боковыми стенками или накрывают их сверху и снизу?')
    referenced = dimension_references(cabinet)
    unused = [d for d in plan.dimensions if d.formula is None and d.value_mm is not None and d.id not in referenced]
    if unused:
        item = unused[0]
        raise MissingConstruction(f'Ранее вы указали «{item.target}»: {item.value_mm:g} мм. Уточните, к какому элементу и расстоянию относится это значение; оно ещё не связано с текущей схемой. Если это дубликат или отменённый размер, укажите это.')
    widths = [require(s.width_dimension, f'Какова ширина секции «{s.label}»?') for s in cabinet.sections]
    total = sum(expression(w, values) for w in widths)
    t = expression(T, values)
    expected = total + (len(widths) + 1) * t if cabinet.width_basis == 'clear_openings' else total
    if not isclose(expected, values['overall_width'], abs_tol=TOLERANCE):
        raise MissingConstruction(f'Ширины секций с учётом стенок дают {expected:g} мм, а ширина шкафа — {values["overall_width"]:g} мм. Уточните ширины или способ их измерения.')
    inner_top = combine(H, neg(T))
    wall_bottom, wall_top = ([], H) if cabinet.joint == 'between_sides' else (T, inner_top)
    roof_left, roof_right = (T, combine(W, neg(T))) if cabinet.joint == 'between_sides' else ([], W)
    part('body_left', 'Левая боковина', 'panel', pt(y=wall_bottom), pt(T, wall_top, D))
    part('body_right', 'Правая боковина', 'panel', pt(combine(W, neg(T)), wall_bottom), pt(W, wall_top, D))
    part('body_bottom', 'Дно', 'panel', pt(roof_left), pt(roof_right, T, D))
    part('body_roof', 'Крыша', 'panel', pt(roof_left, inner_top), pt(roof_right, H, D))
    measure('overall_width', 'x', pt(), pt(W))
    measure('overall_height', 'y', pt(), pt(y=H))
    measure('overall_depth', 'z', pt(), pt(z=D), 'side')
    measure(cabinet.panel_thickness_dimension, 'x', pt(y=wall_bottom), pt(T, wall_bottom))
    if F:
        measure(cabinet.facade_thickness_dimension, 'z', pt(), pt(z=F), 'side')

    cumulative, next_clear = [], T
    for i, section in enumerate(cabinet.sections):
        width = widths[i]
        if cabinet.width_basis == 'partition_centers':
            chain_start = cumulative
            cumulative = combine(cumulative, width)
            left = T if i == 0 else combine(chain_start, half(T))
            right = combine(W, neg(T)) if i == len(widths)-1 else combine(cumulative, neg(half(T)))
            dimension_start, dimension_end = chain_start, cumulative
            divider_left, divider_right = combine(cumulative, neg(half(T))), combine(cumulative, half(T))
        else:
            left = next_clear
            right = combine(left, width)
            dimension_start, dimension_end = left, right
            divider_left, divider_right = right, combine(right, T)
            next_clear = divider_right
        measure(section.width_dimension, 'x', pt(dimension_start), pt(dimension_end))
        if i < len(widths)-1:
            part('divider_' + section.id, 'Перегородка после ' + section.label, 'divider',
                 pt(divider_left, T), pt(divider_right, inner_top, D))
        previous_top = None
        for element in section.elements:
            if element.kind != 'drawer_front' and element.height_dimension is not None:
                raise GeometryError('Высоту фасада можно задавать только ящику; полке нужна толщина панели, а промежуток задаётся offset_dimension.')
            if element.kind == 'rod':
                height = []
            elif element.kind == 'shelf':
                height = T
            else:
                height = require(element.height_dimension, f'Какова высота фасада «{element.label}»?')
            offset = require(element.offset_dimension, f'Уточните расстояние для «{element.label}».') if element.offset_dimension else []
            if element.position_reference == 'floor':
                anchor, bottom = [], offset
            elif element.position_reference == 'inner_bottom':
                anchor, bottom = T, combine(T, offset)
            elif element.position_reference == 'previous_top' and previous_top is not None:
                anchor, bottom = previous_top, combine(previous_top, offset)
            elif element.position_reference == 'top':
                anchor, bottom = H, combine(H, neg(offset), neg(height))
            else:
                raise MissingConstruction(f'Где расположен элемент «{element.label}»: от какого края или соседнего элемента измерено расстояние?')
            top = combine(bottom, height)
            previous_top = top
            if element.width_mode == 'opening':
                lowx, highx = left, right
            elif element.width_mode == 'dimension':
                ew = require(element.width_dimension, f'Какова ширина «{element.label}»?')
                if element.alignment == 'left':
                    lowx = left
                elif element.alignment == 'right':
                    lowx = combine(right, neg(ew))
                elif element.alignment == 'center':
                    lowx = combine(left, half(combine(right, neg(left), neg(ew))))
                else:
                    raise MissingConstruction(f'Элемент «{element.label}» уже проёма. Он расположен слева, справа или по центру?')
                highx = combine(lowx, ew)
            else:
                raise MissingConstruction(f'Элемент «{element.label}» занимает всю ширину проёма или должен быть уже?')
            if expression(lowx, values) < expression(left, values)-TOLERANCE or expression(highx, values) > expression(right, values)+TOLERANCE:
                raise MissingConstruction(f'Элемент «{element.label}» шире свободного проёма. Уточните ширину с учётом толщины стенок.')
            if expression(bottom, values) < expression(T, values)-TOLERANCE or expression(top, values) > expression(inner_top, values)+TOLERANCE:
                raise MissingConstruction(f'Элемент «{element.label}» не помещается по высоте между дном и крышей. Уточните высоты и промежутки.')
            if element.kind == 'shelf':
                depth = require(element.depth_dimension, f'Какова глубина полки «{element.label}»?')
                if expression(depth, values) > values['overall_depth'] + TOLERANCE:
                    raise MissingConstruction(f'Глубина полки «{element.label}» больше глубины шкафа. Уточните глубину.')
            elif element.kind == 'drawer_front':
                depth = F
            else:
                depth = []  # Symbolic rod: front view only; depth is not invented.
            part(element.id, element.label, element.kind, pt(lowx, bottom), pt(highx, top, depth))
            ident = element.width_dimension if element.width_mode == 'dimension' else 'calc_' + element.id + '_w'
            if element.width_mode == 'dimension':
                measure(ident, 'x', pt(lowx, bottom), pt(highx, bottom))
            else:
                derived(ident, 'Ширина: ' + element.label, 'width', combine(highx, neg(lowx)), pt(lowx, bottom), pt(highx, bottom))
            if element.offset_dimension:
                endpoint = top if element.position_reference == 'top' else bottom
                measure(element.offset_dimension, 'y', pt(lowx, anchor), pt(lowx, endpoint))
            if element.kind == 'drawer_front':
                measure(element.height_dimension, 'y', pt(lowx, bottom), pt(lowx, top))
            if element.kind == 'shelf':
                measure(element.depth_dimension, 'z', pt(lowx, bottom), pt(lowx, bottom, depth), 'side')
        if section.door:
            door = section.door
            dw = require(door.width_dimension, f'Какова ширина двери «{door.label}»?')
            dh = require(door.height_dimension, f'Какова высота двери «{door.label}»?')
            db = require(door.bottom_offset_dimension, f'Каков отступ двери «{door.label}» от низа корпуса?') if door.bottom_offset_dimension else []
            # Door centered on its section including the shared panel halves.
            center = half(combine(dimension_start, dimension_end))
            dl, dr = combine(center, neg(half(dw))), combine(center, half(dw))
            dt = combine(db, dh)
            part(door.id, door.label, 'door', pt(dl, db), pt(dr, dt, F))
            measure(door.width_dimension, 'x', pt(dl, db), pt(dr, db))
            measure(door.height_dimension, 'y', pt(dl, db), pt(dl, dt))
            if door.bottom_offset_dimension:
                measure(door.bottom_offset_dimension, 'y', pt(dl), pt(dl, db))
    # Preserve unused old inputs in the project, but only dimensions attached to
    # this drawing belong on its sheet. Never delete customer inputs from storage.
    selected = [d for d in plan.dimensions if d.id in used and not d.id.startswith('calc_')]
    selected.extend(generated)
    if len(selected) > 120 or len(measures) > 120 or len(parts) > 80:
        raise MissingConstruction('Слишком много деталей или размеров для одного листа. Разделите изделие на несколько проектов.')
    return Drawing(format='orthogonal-v1', parts=parts, measures=measures), selected
