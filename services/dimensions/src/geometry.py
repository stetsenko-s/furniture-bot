"""Pure calculation and validation shared by every projection of one drawing."""
from dataclasses import dataclass
from math import isclose, isfinite

TOLERANCE = 0.05  # mm; floating-point tolerance, not a manufacturing allowance
AXES = {'front': ('x', 'y'), 'side': ('z', 'y'), 'top': ('x', 'z')}


class GeometryError(ValueError):
    pass


def expression(terms, values):
    result = 0.0
    for term in terms:
        if term.dimension not in values or values[term.dimension] is None:
            raise GeometryError('Не определён размер: ' + term.dimension)
        result += values[term.dimension] * term.coefficient
    if not isfinite(result):
        raise GeometryError('Расчёт дал недопустимое число.')
    return result


def calculate(dimensions):
    """Resolve a DAG; never use the numeric answer supplied for a formula by AI."""
    by_id = {d.id: d for d in dimensions}
    if len(by_id) != len(dimensions):
        raise GeometryError('Повторяются обозначения размеров.')
    values, visiting = {}, set()

    def resolve(ident):
        if ident in values:
            return values[ident]
        if ident not in by_id:
            raise GeometryError('Формула ссылается на отсутствующий размер: ' + ident)
        if ident in visiting:
            raise GeometryError('Размеры зависят друг от друга по кругу: ' + ident)
        visiting.add(ident)
        item = by_id[ident]
        if item.formula is None:
            value = item.value_mm
        else:
            dependencies = {t.dimension: resolve(t.dimension) for t in item.formula}
            value = None if None in dependencies.values() else expression(item.formula, dependencies)
        if value is not None and (not isfinite(value) or value <= 0):
            raise GeometryError('Размер «' + item.target + '» получился нулевым или отрицательным. Проверьте конструкцию и зазоры.')
        visiting.remove(ident)
        values[ident] = value
        return value

    for ident in by_id:
        resolve(ident)
    return values


def point(value, values):
    return tuple(expression(getattr(value, a), values) for a in ('x', 'y', 'z'))


@dataclass(frozen=True)
class Box:
    id: str
    label: str
    kind: str
    low: tuple
    high: tuple


@dataclass(frozen=True)
class Measurement:
    dimension: str
    view: str
    axis: str
    start: tuple
    end: tuple
    value: float


@dataclass(frozen=True)
class Scene:
    boxes: tuple
    measures: tuple
    size: tuple


def projection(box, view):
    """Same box and scale for every view; no independently generated facades."""
    indices = tuple('xyz'.index(a) for a in AXES[view])
    return tuple(box.low[i] for i in indices), tuple(box.high[i] for i in indices)


def compile_scene(plan):
    if plan.drawing is None:
        raise GeometryError('Для этого проекта ещё нет схемы конструкции. Нужен повторный анализ перед нанесением размеров.')
    values = calculate(plan.dimensions)
    if None in values.values():
        raise GeometryError('Сначала уточните исходные размеры.')
    for d in plan.dimensions:
        if d.value_mm is None or not isclose(d.value_mm, values[d.id], abs_tol=TOLERANCE):
            raise GeometryError('Расчёт размера «' + d.target + '» устарел. Повторите анализ.')
        if d.id.startswith('overall_') and d.formula is not None:
            raise GeometryError('Габариты должны быть подтверждены как исходные размеры.')
    size = tuple(values.get('overall_' + a) for a in ('width', 'height', 'depth'))
    if any(v is None for v in size):
        raise GeometryError('Не заданы габариты изделия.')
    boxes, ids = [], set()
    for part in plan.drawing.parts:
        if part.id in ids:
            raise GeometryError('Повторяется элемент конструкции: ' + part.label)
        ids.add(part.id)
        low, high = point(part.low, values), point(part.high, values)
        symbolic_rod = part.kind == 'rod' and low[1:] == high[1:]
        for index, (a, b, limit) in enumerate(zip(low, high, size)):
            flat_allowed = symbolic_rod and index in (1, 2)
            if (b < a or (b == a and not flat_allowed)) or a < -TOLERANCE or b > limit + TOLERANCE:
                raise GeometryError('Элемент «' + part.label + '» выходит за габариты или имеет неверный размер. Уточните его расположение.')
        boxes.append(Box(part.id, part.label, part.kind, low, high))
    # Facades are a separate projection layer; their overlaps with the body are
    # intentional. Two facades in the same front plane must not intersect.
    facades = {'door', 'drawer_front'}
    for i, first in enumerate(boxes):
        for second in boxes[i + 1:]:
            if (first.kind in facades) != (second.kind in facades):
                continue
            overlap = [min(a, b) - max(c, d) for a, b, c, d in zip(first.high, second.high, first.low, second.low)]
            if all(v > TOLERANCE for v in overlap):
                # A closed door can cover drawers in its section.
                if {first.kind, second.kind} == {'door', 'drawer_front'}:
                    continue
                raise GeometryError('Пересекаются элементы «' + first.label + '» и «' + second.label + '». Уточните размеры и расположение.')
    measures, covered = [], set()
    dimensions = {d.id: d for d in plan.dimensions}
    for m in plan.drawing.measures:
        if m.dimension not in values:
            raise GeometryError('Подпись ссылается на отсутствующий размер: ' + m.dimension)
        if m.axis not in AXES[m.view]:
            raise GeometryError('Размер нельзя показать на выбранном виде: ' + m.dimension)
        expected_axis = {'width': 'x', 'height': 'y', 'depth': 'z'}.get(dimensions[m.dimension].axis)
        if expected_axis and m.axis != expected_axis:
            raise GeometryError('Ось размерной линии противоречит описанию: ' + m.dimension)
        start, end = point(m.start, values), point(m.end, values)
        for p in (start, end):
            if any(v < -TOLERANCE or v > lim + TOLERANCE for v, lim in zip(p, size)):
                raise GeometryError('Размерная линия выходит за изделие: ' + m.dimension)
        axis = 'xyz'.index(m.axis)
        if any(not isclose(start[i], end[i], abs_tol=TOLERANCE) for i in range(3) if i != axis):
            raise GeometryError('Размерная линия не параллельна оси: ' + m.dimension)
        if not isclose(abs(end[axis] - start[axis]), values[m.dimension], abs_tol=TOLERANCE):
            raise GeometryError('Размерная линия не совпадает с рассчитанным значением: ' + m.dimension)
        measures.append(Measurement(m.dimension, m.view, m.axis, start, end, values[m.dimension]))
        covered.add(m.dimension)
    missing = [d.target for d in plan.dimensions if d.id not in covered]
    if missing:
        raise GeometryError('В схеме не привязаны размерные линии: ' + ', '.join(missing[:4]))
    return Scene(tuple(boxes), tuple(measures), size)


def formula_key(terms):
    if terms is None:
        return None
    totals = {}
    for term in terms:
        totals[term.dimension] = totals.get(term.dimension, 0) + term.coefficient
    return tuple(sorted((key, value) for key, value in totals.items() if value))


def drawing_key(drawing):
    def key(p):
        return tuple(formula_key(getattr(p, axis)) for axis in 'xyz')
    return (tuple(sorted((p.id, p.kind, key(p.low), key(p.high)) for p in drawing.parts)),
            tuple(sorted((m.dimension, m.view, m.axis, key(m.start), key(m.end)) for m in drawing.measures)))


def preserve_structure(plan, previous):
    """Dimensions can change; existing geometry and calculation rules cannot drift."""
    plan.drawing_locked = bool(previous and previous.drawing_locked)
    if not previous or previous.drawing is None:
        return
    old_inventory = sorted((p.id, p.kind) for p in previous.drawing.parts)
    new_inventory = sorted((p.id, p.kind) for p in plan.drawing.parts) if plan.drawing else []
    changed = (new_inventory != old_inventory or
               (previous.drawing_locked and plan.drawing is not None and drawing_key(plan.drawing) != drawing_key(previous.drawing)))
    if changed:
        plan.drawing = previous.drawing.model_copy(deep=True)
        plan.needs_sketch_changes = True
        plan.questions.append('Изменилась схема деталей. Для изменения конструкции вернитесь к эскизу; прежняя схема сохранена.')
    elif previous.drawing_locked:
        plan.drawing = previous.drawing.model_copy(deep=True)
        plan.cabinet = previous.cabinet.model_copy(deep=True) if previous.cabinet else None
    old = {d.id: d for d in previous.dimensions}
    for dimension in plan.dimensions:
        if previous.drawing_locked and dimension.id in old and formula_key(dimension.formula) != formula_key(old[dimension.id].formula):
            dimension.formula = old[dimension.id].formula
            plan.needs_sketch_changes = True
            plan.questions.append('Изменились правила расчёта деталей. Подтвердите новую конструкцию на этапе эскиза.')
