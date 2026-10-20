"""Рендеринг утверждённого изделия; собственный диалог о внешнем виде."""
from pathlib import Path
from contracts.workflow import StagePlan

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'


class RenderProvider:
    def __init__(self, ai):
        self.ai = ai
        self.analysis_prompt = (PROMPTS / 'analysis.md').read_text(encoding='utf-8')
        self.image_prompt = (PROMPTS / 'image.md').read_text(encoding='utf-8')

    async def analyse(self, request):
        data = request.model_dump(exclude={'references'})
        data['messages'] = data['messages'][-80:]
        plan = await self.ai.analyse(self.analysis_prompt, data, request.references, StagePlan)
        # Утверждённые размеры нельзя изменить при настройке внешнего вида.
        original = request.plan
        if original is None:
            plan.questions.append('Сначала утвердите чертёж с размерами.')
        else:
            if [(d.id, d.target, d.axis, d.value_mm) for d in plan.dimensions] != [(d.id, d.target, d.axis, d.value_mm) for d in original.dimensions]:
                plan.questions.append('Для изменения размеров вернитесь на этап размеров.')
            plan.dimensions = original.model_copy(deep=True).dimensions
            plan.inventory_complete = original.inventory_complete
        for axis in ('width_mm', 'height_mm', 'depth_mm'):
            if getattr(plan.brief, axis) != getattr(request.brief, axis):
                plan.questions.append('Для изменения габаритов вернитесь на этап размеров.')
            setattr(plan.brief, axis, getattr(request.brief, axis))
        for name, question in (('material', 'Какой материал использовать для рендеринга?'), ('color', 'Какой цвет или декор показать?')):
            if not (getattr(plan.brief, name) or '').strip():
                plan.questions.append(question)
        if plan.needs_sketch_changes:
            plan.questions.append('Для изменения конструкции вернитесь к эскизу.')
        plan.questions = list(dict.fromkeys(plan.questions))
        return plan

    @staticmethod
    def validate_generation(plan):
        if plan.questions or plan.needs_sketch_changes or not plan.inventory_complete:
            raise ValueError('Есть нерешённые вопросы')
        if not plan.brief.material or not plan.brief.color or not plan.dimensions or any(d.value_mm is None for d in plan.dimensions):
            raise ValueError('Не хватает данных для рендеринга')

    async def generate(self, request):
        self.validate_generation(request.plan)
        return await self.ai.image(self.image_prompt, request.plan, request.references)
