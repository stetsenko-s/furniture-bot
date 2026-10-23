"""Уточнение и нанесение размеров. Не меняет состав утверждённого изделия."""
from pathlib import Path
import asyncio
import base64
from contracts.models import ReferenceImage
from contracts.workflow import StagePlan, Dimension
from infrastructure.stage_ai import AIError
from .geometry import calculate, compile_scene, preserve_structure, GeometryError
from .cabinet import build as build_cabinet, MissingConstruction, UnlinkedDimensions, construction_key, dimension_references, legacy_unlinked_question
from .reconciliation import merge_aliases, merge_position_duplicates, apply_links
from .models import CabinetAnalysis, DimensionLinkRepair
from .evidence import numeric_quote, rejection_quote, rejected_dimensions, analysis_context, is_quantity, normalize

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
AXES = {'width': 'ширина', 'height': 'высота', 'depth': 'глубина',
        'gap': 'расстояние', 'offset': 'положение', 'thickness': 'толщина'}


def checked_plan(plan, request):
    removals = getattr(plan, 'removed_dimensions', [])
    aliases = getattr(plan, 'dimension_aliases', [])
    plan = StagePlan.model_validate(plan.model_dump(exclude={'removed_dimensions', 'dimension_aliases'}))
    if plan.cabinet is None:
        preserve_structure(plan, request.plan)
    else:
        plan.drawing_locked = bool(request.plan and request.plan.drawing_locked)
        if plan.drawing_locked and request.plan.drawing is not None:
            plan.drawing = request.plan.drawing.model_copy(deep=True)
    questions = list(plan.questions)
    previous = {d.id: d for d in request.plan.dimensions} if request.plan else {}
    removed = set(rejected_dimensions(request))
    for removal in removals:
        if removal.id in previous and not removal.id.startswith('overall_'):
            if rejection_quote(removal.source_quote, request.messages):
                removed.add(removal.id)
            else:
                questions.append(f'Подтвердите, нужно ли исключить размер «{previous[removal.id].target}»: не найдено вашего указания об этом.')
    denied_targets = {normalize(previous[ident].target) for ident in removed}
    if plan.cabinet is not None:
        referenced = dimension_references(plan.cabinet)
        # Do not ask about invented unknown dimensions outside the actual scheme.
        removed.update(d.id for d in plan.dimensions if d.value_mm is None and d.id not in referenced)
    for item in plan.dimensions:
        if is_quantity(item) or normalize(item.target) in denied_targets:
            removed.add(item.id)
    plan.dimensions = [d for d in plan.dimensions if d.id not in removed]
    present = {d.id for d in plan.dimensions}
    supplied = {(normalize(d.target), d.axis, d.value_mm) for d in plan.dimensions}
    for ident, old in previous.items():
        # Ответ агента — полный пересобранный план, а не набор изменений.
        # Восстанавливаем только подтверждённые числа; прежние неизвестные
        # пункты могли быть ошибочными догадками или дубликатами другого размера.
        if (ident not in present and ident not in removed and old.value_mm is not None and not is_quantity(old)
                and (normalize(old.target), old.axis, old.value_mm) not in supplied):
            plan.dimensions.append(old.model_copy(deep=True))
    found = set()
    for item in plan.dimensions:
        if item.id in found:
            questions.append(f'Уточните отдельные обозначения размеров: {item.target}.')
        found.add(item.id)
        old = previous.get(item.id)
        if (old and old.value_mm is not None and item.value_mm is None
                and (item.target, item.axis) == (old.target, old.axis)
                and (not item.source_quote or item.source_quote == old.source_quote)):
            item.value_mm, item.source_quote = old.value_mm, old.source_quote
        trusted = old and (old.target, old.axis, old.value_mm) == (item.target, item.axis, item.value_mm) and old.value_mm is not None
        if item.id in ('overall_width', 'overall_height', 'overall_depth'):
            axis = item.id.removeprefix('overall_')
            trusted = trusted or (item.axis == axis and item.value_mm == getattr(request.brief, axis + '_mm'))
        evidenced = numeric_quote(item.value_mm, item.source_quote, request.messages)
        if item.formula is not None:
            item.value_mm = None
            item.source_quote = None
        elif item.value_mm is not None and not (trusted or evidenced):
            item.value_mm = None
    try:
        values = calculate(plan.dimensions)
        for item in plan.dimensions:
            item.value_mm = values[item.id]
    except GeometryError as exc:
        questions.append(str(exc))
    merge_position_duplicates(plan, request.messages)
    merge_aliases(plan, aliases, request.messages)
    for item in plan.dimensions:
        if item.value_mm is None and item.formula is None:
            questions.append(f'Укажите размер «{item.target}» в миллиметрах.')
    # Агент может скопировать прежний программный вопрос. Удаляем только
    # известные шаблоны для уже отвеченных или явно исключённых пунктов.
    resolved = removed | {d.id for d in plan.dimensions if d.value_mm is not None} | (previous.keys() - {d.id for d in plan.dimensions})
    stale_questions = set()
    for old in list(previous.values()) + plan.dimensions:
        if old.value_mm is not None:
            # Recompute this server diagnostic; never echo it as a client question.
            stale_questions.add(legacy_unlinked_question(old))
        if old.id in resolved:
            stale_questions.update((
                f'Укажите в миллиметрах размер «{old.target}» ({AXES[old.axis]}).',
                f'Укажите размер «{old.target}» в миллиметрах.',
            ))
    questions = [q for q in questions if q.strip() not in stale_questions]
    for axis in ('width', 'height', 'depth'):
        ident = 'overall_' + axis
        item = next((d for d in plan.dimensions if d.id == ident), None)
        if item is None:
            value = getattr(request.brief, axis + '_mm')
            item = Dimension(id=ident, target={'width': 'Ширина изделия', 'height': 'Высота изделия', 'depth': 'Глубина изделия'}[axis], axis=axis, value_mm=value, source_quote=None)
            plan.dimensions.insert(0, item)
        if item.axis != axis:
            item.axis = axis
            item.value_mm = None
            questions.append(f'Уточните {item.target.lower()}: обозначения размера противоречат друг другу.')
        stated = getattr(plan.brief, axis + '_mm')
        if stated is not None and item.value_mm is not None and stated != item.value_mm:
            questions.append(f'Уточните {item.target.lower()}: в описании {stated:g} мм, в списке {item.value_mm:g} мм.')
        setattr(plan.brief, axis + '_mm', item.value_mm)
        if item.value_mm is None:
            questions.append(f'Укажите {item.target.lower()} в миллиметрах.')
    if not plan.inventory_complete:
        questions.append('Уточните состав и расположение внутренних элементов и фасадов, для которых нужны размеры.')
    if plan.needs_sketch_changes:
        questions.append('Изменяется конструкция. Вернитесь к эскизу и утвердите новый вариант.')
    for item in plan.dimensions:
        if not item.id.startswith('overall_') and item.axis in ('width', 'height', 'depth'):
            overall = getattr(plan.brief, item.axis + '_mm')
            if overall is not None and item.value_mm is not None and item.value_mm > overall:
                questions.append(f'Размер «{item.target}» ({item.value_mm:g} мм) превышает габарит {overall:g} мм. Уточните значение.')
    plan.questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))
    if plan.questions and plan.questions != list(request.plan.questions if request.plan else []):
        # Модель не должна сообщать «все данные подтверждены», если проверка
        # отклонила выдуманную цитату/расчёт и добавила вопросы.
        plan.reply = 'Уточнения сохранены. Перед нанесением размеров нужно разрешить вопросы ниже.'
    elif plan.questions:
        plan.reply = 'Уточнения сохранены. Часть размеров пока требует уточнения.'
    return plan


class DimensionsProvider:
    def __init__(self, ai):
        self.ai = ai
        self.analysis_prompt = (PROMPTS / 'analysis.md').read_text(encoding='utf-8')
        self.analysis_prompt += '\n\n' + (PROMPTS / 'cabinet.md').read_text(encoding='utf-8')
        self.links_prompt = (PROMPTS / 'links.md').read_text(encoding='utf-8')

    async def analyse(self, request):
        data = analysis_context(request)
        repaired_plan = None
        for attempt in range(2):
            if repaired_plan is None:
                raw = await self.ai.analyse(self.analysis_prompt, data, request.references, CabinetAnalysis)
                plan = checked_plan(raw, request)
            else:
                plan = repaired_plan
            if (request.plan and request.plan.drawing_locked and request.plan.cabinet is not None
                    and construction_key(plan.cabinet) != construction_key(request.plan.cabinet)):
                plan.cabinet = request.plan.cabinet.model_copy(deep=True)
                plan.drawing = request.plan.drawing.model_copy(deep=True)
                plan.drawing_locked = True
                plan.needs_sketch_changes = True
                plan.questions.append('Изменяется устройство шкафа. Вернитесь к эскизу и утвердите новую конструкцию.')
            if plan.questions:
                plan.reply = 'Данные сохранены. Для построения согласованных видов нужно уточнить вопросы ниже.'
                return plan
            try:
                if plan.cabinet is not None:
                    plan.drawing, plan.dimensions = build_cabinet(plan)
                    preserve_structure(plan, request.plan)
                    if plan.needs_sketch_changes:
                        return plan
                elif plan.drawing is None:
                    raise MissingConstruction('Для размерного листа нужна схема прямоугольного шкафа с секциями. Опишите его секции; сложные и наклонные конструкции пока не поддерживаются.')
                compile_scene(plan)
                plan.drawing_locked = True
            except MissingConstruction as exc:
                if request.plan and request.plan.drawing_locked:
                    plan.drawing = request.plan.drawing.model_copy(deep=True)
                    plan.drawing_locked = True
                else:
                    plan.drawing = None
                    plan.drawing_locked = False
                plan.questions = [str(exc)]
                plan.reply = 'Размеры сохранены. Уточним устройство шкафа, чтобы рассчитать детали без догадок.'
                return plan
            except UnlinkedDimensions as exc:
                if attempt:
                    raise AIError('Модель не связала сохранённые размеры', 'invalid_geometry') from exc
                repair_data = {'cabinet': plan.cabinet.model_dump(),
                    'dimensions': [d.model_dump() for d in plan.dimensions if d.formula is None],
                    'unlinked_dimensions': exc.items,
                    'messages': data['messages'][-30:]}
                repair = await self.ai.analyse(self.links_prompt, repair_data, [], DimensionLinkRepair)
                if not isinstance(repair, DimensionLinkRepair):
                    raise AIError('Некорректный ответ исправления связей', 'invalid_model_response')
                try:
                    apply_links(plan, repair, request.messages)
                except GeometryError as link_error:
                    raise AIError('Некорректная привязка размера', 'invalid_geometry') from link_error
                repaired_plan = plan
                continue
            except GeometryError as exc:
                if attempt:
                    raise AIError('Модель дважды вернула некорректную геометрию', 'invalid_geometry') from exc
                # Repair model-generated coordinates, not the customer's numbers.
                # The first candidate was never approved, so it is not a new baseline.
                data['geometry_feedback'] = str(exc)
                data['rejected_candidate'] = plan.model_dump()
                data['rejected_candidate']['drawing'] = None
                data['rejected_candidate']['dimensions'] = [d.model_dump() for d in plan.dimensions if d.formula is None]
                continue
            plan.reply = 'Схема и размеры проверены. Проверьте состав деталей; все виды будут построены из одной схемы.'
            return plan

    @staticmethod
    def validate_generation(plan):
        if plan.questions or plan.needs_sketch_changes or not plan.inventory_complete or not plan.dimensions:
            raise ValueError('Есть нерешённые вопросы')
        if any(d.value_mm is None for d in plan.dimensions):
            raise ValueError('Есть неизвестные размеры')
        if len({d.id for d in plan.dimensions}) != len(plan.dimensions):
            raise ValueError('Повторяются обозначения')
        for axis in ('width', 'height', 'depth'):
            item = next((d for d in plan.dimensions if d.id == 'overall_' + axis and d.axis == axis), None)
            if item is None or item.value_mm != getattr(plan.brief, axis + '_mm'):
                raise ValueError('Габариты не согласованы')
        compile_scene(plan)

    async def generate(self, request):
        self.validate_generation(request.plan)
        # Never send an approved construction back to an image generator.
        from .drawing import render_png, MODEL
        compile_scene(request.plan)
        data = await asyncio.to_thread(render_png, request.plan)
        return ReferenceImage(mime_type='image/png', data_base64=base64.b64encode(data).decode()), MODEL
