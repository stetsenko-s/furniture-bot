"""Уточнение и нанесение размеров. Не меняет состав утверждённого изделия."""
from pathlib import Path
from contracts.workflow import StagePlan, Dimension
from .models import DimensionsAnalysis
from .evidence import numeric_quote, rejection_quote, rejected_dimensions, analysis_context, is_quantity, normalize

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
AXES = {'width': 'ширина', 'height': 'высота', 'depth': 'глубина',
        'gap': 'расстояние', 'offset': 'положение', 'thickness': 'толщина'}


def checked_plan(plan, request):
    removals = getattr(plan, 'removed_dimensions', [])
    plan = StagePlan.model_validate(plan.model_dump(exclude={'removed_dimensions'}))
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
        if item.value_mm is not None and not (trusted or evidenced):
            item.value_mm = None
        if item.value_mm is None:
            questions.append(f'Укажите размер «{item.target}» в миллиметрах.')
    # Агент может скопировать прежний программный вопрос. Удаляем только
    # известные шаблоны для уже отвеченных или явно исключённых пунктов.
    resolved = removed | {d.id for d in plan.dimensions if d.value_mm is not None} | (previous.keys() - {d.id for d in plan.dimensions})
    stale_questions = set()
    for old in list(previous.values()) + plan.dimensions:
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
        self.image_prompt = (PROMPTS / 'image.md').read_text(encoding='utf-8')

    async def analyse(self, request):
        data = analysis_context(request)
        plan = await self.ai.analyse(self.analysis_prompt, data, request.references, DimensionsAnalysis)
        return checked_plan(plan, request)

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

    async def generate(self, request):
        self.validate_generation(request.plan)
        return await self.ai.image(self.image_prompt, request.plan, request.references)
