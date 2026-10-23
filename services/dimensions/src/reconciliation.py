"""Validate explicit aliases proposed by the model for duplicate input names."""
from .cabinet import dimension_references, legacy_unlinked_question
import re
from .evidence import user_quote, normalize
from .geometry import GeometryError
from .models import DimensionAlias


def directions(text):
    return set(re.findall(r'лев|прав|средн|центральн', normalize(text)))


def merge_position_duplicates(plan, messages):
    """Legacy height/offset duplicates for a uniquely identified bottom drawer."""
    if not plan.cabinet:
        return
    dimensions = {d.id: d for d in plan.dimensions}
    referenced = dimension_references(plan.cabinet)
    aliases = []
    for section in plan.cabinet.sections:
        drawers = [e for e in section.elements if e.kind == 'drawer_front']
        if len(drawers) != 1 or not directions(section.label):
            continue
        drawer = drawers[0]
        canonical = dimensions.get(drawer.offset_dimension)
        if (drawer.position_reference != 'floor' or canonical is None
                or canonical.axis != 'offset' or not canonical.source_quote
                or canonical.value_mm is None):
            continue
        for duplicate in plan.dimensions:
            label = normalize(duplicate.target)
            if (duplicate.id in referenced or duplicate.axis not in ('height', 'offset')
                    or duplicate.value_mm != canonical.value_mm or not duplicate.source_quote
                    or normalize(duplicate.source_quote) != normalize(canonical.source_quote)
                    or directions(duplicate.target) != directions(section.label)
                    or not re.search(r'ящик', label)
                    or not re.search(r'положени|отступ', label)
                    or not re.search(r'от (?:низа|пола)(?:\s|$)', label)):
                continue
            aliases.append(DimensionAlias(duplicate_id=duplicate.id, canonical_id=canonical.id,
                                          source_quote=duplicate.source_quote))
    merge_aliases(plan, aliases, messages)


def merge_aliases(plan, aliases, messages):
    if not plan.cabinet:
        return
    referenced = dimension_references(plan.cabinet)
    dimensions = {d.id: d for d in plan.dimensions}
    removed = set()
    vertical = {'height', 'offset', 'gap'}
    for alias in aliases:
        old = dimensions.get(alias.duplicate_id)
        current = dimensions.get(alias.canonical_id)
        if (old is None or current is None or old.id == current.id
                or old.id.startswith('overall_') or current.id.startswith('overall_')
                or old.id in referenced or current.id not in referenced
                or old.formula is not None or current.formula is not None
                or old.value_mm is None or old.value_mm != current.value_mm):
            continue
        if old.axis != current.axis and not {old.axis, current.axis} <= vertical:
            continue
        if directions(old.target) != directions(current.target):
            continue
        if old.axis != current.axis:
            height_items = [d for d in (old, current) if d.axis == 'height']
            if any(not re.search(r'положени|отступ|от (?:низа|пола|верха)', normalize(d.target)) for d in height_items):
                continue
        # Equality alone is insufficient. The model must identify one measurement
        # and provide a real customer quote shared by both saved records.
        if not user_quote(alias.source_quote, messages):
            continue
        if not (old.source_quote and current.source_quote):
            continue
        quote = normalize(alias.source_quote)
        if quote not in normalize(old.source_quote) or quote not in normalize(current.source_quote):
            continue
        removed.add(old.id)
    plan.dimensions = [d for d in plan.dimensions if d.id not in removed]


def apply_links(plan, repair, messages):
    """Apply only missing shelf-gap links, with no new parts or numeric values."""
    if plan.drawing_locked:
        raise GeometryError('Утверждённая схема не допускает изменения привязок.')
    sections = {s.id: s for s in plan.cabinet.sections}
    dimensions = {d.id: d for d in plan.dimensions}
    pending = []
    edits = []
    edited_ids = set()
    for link in repair.shelf_gaps:
        section = sections.get(link.section_id)
        dimension = dimensions.get(link.dimension_id)
        if (section is None or dimension is None or dimension.value_mm is None
                or dimension.axis not in ('height', 'offset', 'gap')
                or not user_quote(link.source_quote, messages)):
            raise GeometryError('Недостоверная привязка промежутка.')
        ids = [e.id for e in section.elements]
        if len(set(link.shelf_ids)) != len(link.shelf_ids) or any(i not in ids for i in link.shelf_ids):
            raise GeometryError('Не найдены указанные полки.')
        indexes = [ids.index(i) for i in link.shelf_ids]
        if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
            raise GeometryError('Полки для промежутка должны идти последовательно.')
        shelves = [section.elements[i] for i in indexes]
        if any(e.kind != 'shelf' for e in shelves):
            raise GeometryError('Промежуток между полками нельзя назначить ящику или штанге.')
        if directions(dimension.target) and directions(section.label) and directions(dimension.target) != directions(section.label):
            raise GeometryError('Размер относится к другой секции.')
        quote = normalize(link.source_quote)
        if link.basis == 'clear_gap' and not re.search(r'просвет|чист\w*\s+(?:расстоян|промежут|зазор)', quote):
            link.basis = None
        if link.basis == 'pitch' and not re.search(r'шаг|между\s+(?:их\s+)?(?:верхними|нижними)\s+(?:поверхност|плоскост)', quote):
            link.basis = None
        if link.basis is None:
            pending.append(f'Для секции «{section.label}» сохранено {dimension.value_mm:g} мм между полками. Это чистый просвет между полками или расстояние между их верхними поверхностями?')
            continue
        for shelf in shelves[1:]:
            if (shelf.id in edited_ids or shelf.offset_dimension not in (None, dimension.id)
                    or shelf.position_reference not in (None, 'previous_top', 'previous_bottom')):
                raise GeometryError('Нельзя заменить другую заданную привязку полки.')
            edited_ids.add(shelf.id)
            edits.append((shelf, dimension.id, 'previous_top' if link.basis == 'clear_gap' else 'previous_bottom'))
    for shelf, ident, position in edits:
        shelf.offset_dimension = ident
        shelf.position_reference = position
    merge_aliases(plan, repair.dimension_aliases, messages)
    stale = {legacy_unlinked_question(d) for d in plan.dimensions if d.value_mm is not None}
    plan.questions = list(dict.fromkeys(pending + [q for q in repair.questions if q not in stale]))
