"""Последовательность утверждений и диалог на этапах размеров/рендеринга."""
from .workflow_store import WorkflowStore
from .stage_client import StageError

LABELS = {'dimensions': 'Чертёж с размерами', 'render': 'Рендер', 'sketch': 'Эскиз'}
AXES = {'width': 'ширина', 'height': 'высота', 'depth': 'глубина', 'gap': 'расстояние', 'offset': 'положение', 'thickness': 'толщина'}


class Workflow:
    def __init__(self, app, dimensions=None, render=None):
        self.app = app
        self.db = WorkflowStore(app.store)
        self.clients = {'dimensions': dimensions, 'render': render}

    def active(self, uid, pid):
        return self.db.state(uid, pid)['stage'] != 'sketch'

    def client(self, stage):
        if self.clients[stage] is None:
            raise StageError(f'Сервис {stage} пока не подключён.')
        return self.clients[stage]

    def sketch_actions(self, uid, project):
        pid, number = project['id'], project['revision']
        if not self.app.store.artifact(uid, pid, number):
            return []
        if self.db.state(uid, pid)['approved_sketch'] == number:
            return [[('Проставить размеры', f'dimensions:{pid}:{number}')]]
        return [[('Утвердить эскиз', f'approve_sketch:{pid}:{number}')]]

    def rows(self, uid, pid):
        v = self.db.version(uid, pid)
        stage, number = v['stage'], v['number']
        code = 'd' if stage == 'dimensions' else 'r'
        rows = []
        legacy = stage == 'dimensions' and v['plan'] and v['plan'].drawing is None and v['plan'].cabinet is None
        if legacy:
            rows.append([('Перестроить чертёж', f'wfretry:{pid}:{code}:{number}')])
        elif v['path']:
            if not v['approved']:
                rows.append([('Утвердить размеры' if stage == 'dimensions' else 'Утвердить рендер', f'wfapprove:{pid}:{code}:{number}')])
            elif stage == 'dimensions':
                rows.append([('Рендеринг', f'render:{pid}:{number}')])
        elif v['error'] or not v['analysed']:
            rows.append([('Попробовать ещё раз', f'wfretry:{pid}:{code}:{number}')])
        elif v['analysed'] and v['plan'] and not v['plan'].questions:
            rows.append([('Нанести размеры' if stage == 'dimensions' else 'Создать рендер', f'wfgen:{pid}:{code}:{number}')])
        if v['plan'] and v['plan'].needs_sketch_changes:
            rows.append([('Изменить эскиз', f'wfsketch:{pid}:{code}:{number}')])
        rows.extend([[('Чертежи', f'history:{pid}')], [('Назад', f'wfback:{pid}:{code}:{number}'), ('Проекты', 'menu')],
                     [('Переименовать', f'rename:{pid}'), ('Удалить', f'delete:{pid}')]])
        return rows

    async def show(self, message, uid, pid):
        from .main import keyboard, brief_text
        v = self.db.version(uid, pid)
        project = self.app.store.project(uid, pid)
        plan = v['plan']
        text = f"{project['title']}\n\nЭтап: {LABELS[v['stage']]}\n\n"
        if plan:
            brief = plan.brief.model_copy(update={'title': project['title']})
            text += brief_text(brief) + '\n\n' + plan.reply
            if v['stage'] == 'dimensions':
                if plan.drawing:
                    text += '\n\nСостав схемы:\n' + '\n'.join('• ' + p.label for p in plan.drawing.parts)
                elif plan.cabinet:
                    text += '\n\nСостав схемы:\n' + '\n'.join(
                        s.label + ': ' + ', '.join([e.label for e in s.elements] + ([s.door.label] if s.door else ['без двери']))
                        for s in plan.cabinet.sections)
                text += '\n\nРазмеры в мм:\n' + '\n'.join((f"• {d.target} ({AXES[d.axis]}): {d.value_mm:g}" + (' — рассчитано' if d.formula else '')) if d.value_mm is not None else f'• {d.target}: нужно уточнить исходные данные' for d in plan.dimensions)
            if not v['analysed']:
                text += '\n\nДополнение сохранено; обработка ещё не завершена.'
            elif plan.questions:
                text += '\n\nНужно уточнить:\n' + '\n'.join(f'{i}. {q}' for i,q in enumerate(plan.questions[:3], 1))
            elif not v['path']:
                text += '\n\nПроверьте данные и нажмите кнопку создания изображения.'
        else:
            text += 'Подготовка данных этапа. Можно прислать уточнения обычным сообщением.'
        if v['path']:
            text += f"\n\n{LABELS[v['stage']]} №{v['ordinal']}. "
            if v['approved']:
                text += 'Утверждён. ' + ('Перейти к рендерингу?' if v['stage'] == 'dimensions' else 'Можно вернуться к проекту или прислать правки.')
            else:
                text += 'Проверьте изображение, напишите правки или утвердите результат.'
            if v['stage'] == 'dimensions':
                if v['model'] == 'orthogonal-drawing-v1':
                    text += '\nВиды и подписи построены из общей схемы. Проверьте, верно ли распознана конструкция. Крепёж и производственная деталировка не рассчитаны.'
                else:
                    text += '\nЭто прежний результат генерации. Нажмите «Перестроить чертёж», чтобы построить согласованные виды из схемы деталей.'
        if v['error']:
            text += '\n\n' + v['error']
        self.app.store.navigate(uid, 'result' if v['path'] else 'review', pid)
        await self.app.say(message, uid, text, keyboard(self.rows(uid, pid)), v['path'])

    async def analyse(self, message, uid, pid):
        async with self.app.lock(uid):
            try:
                initial_stage = self.db.state(uid, pid)['stage']
                while self.db.state(uid, pid)['stage'] == initial_stage and initial_stage != 'sketch':
                    v = self.db.version(uid, pid)
                    if v['analysed']:
                        if self.app.visible(uid, pid):
                            await self.show(message, uid, pid)
                        return
                    if self.app.visible(uid, pid):
                        await self.app.say(message, uid, 'Данные сохранены. Уточняю ' + ('размеры…' if v['stage'] == 'dimensions' else 'внешний вид изделия…'))
                    try:
                        plan = await self.client(v['stage']).analyse(self.db.analysis_request(uid, pid, v))
                    except StageError as exc:
                        if not self.db.current(uid, pid, v['stage'], v['number']):
                            continue
                        self.db.error(uid, pid, v['stage'], v['number'], str(exc))
                        if self.app.visible(uid, pid):
                            await self.show(message, uid, pid)
                        return
                    if self.db.save_analysis(uid, pid, v['stage'], v['number'], plan):
                        if self.app.visible(uid, pid):
                            await self.show(message, uid, pid)
                        return
            except LookupError:
                return

    async def receive(self, message, uid, pid, text, image, mime):
        self.db.add_input(uid, pid, text, image, mime)
        if self.app.lock(uid).locked():
            await self.app.say(message, uid, 'Дополнение сохранено. Учту его после текущей обработки.')
        await self.analyse(message, uid, pid)

    async def generate(self, message, uid, pid, number):
        async with self.app.lock(uid):
            try:
                v = self.db.version(uid, pid)
                request = self.db.request(uid, pid, v['stage'], number)
                if v['path']:
                    await self.show(message, uid, pid)
                    return
                await self.app.say(message, uid, 'Наношу размеры…' if v['stage'] == 'dimensions' else 'Создаю рендер…')
                try:
                    image, model = await self.client(v['stage']).generate(request)
                except StageError as exc:
                    self.db.error(uid, pid, v['stage'], number, str(exc))
                    if self.db.current(uid, pid, v['stage'], number) and self.app.visible(uid, pid):
                        await self.show(message, uid, pid)
                    return
                self.db.save_image(uid, pid, v['stage'], number, image, model)
                if self.db.current(uid, pid, v['stage'], number) and self.app.visible(uid, pid):
                    await self.show(message, uid, pid)
            except LookupError:
                return

    async def retry(self, message, uid, pid):
        self.db.upgrade_drawing(uid, pid)
        v = self.db.version(uid, pid)
        if v['request_json'] and not v['path']:
            await self.generate(message, uid, pid, v['number'])
        else:
            self.db.prepare_reanalysis(uid, pid)
            await self.analyse(message, uid, pid)

    async def handle(self, message, uid, pid, action, parts):
        from .main import keyboard, brief_text
        if action == 'stageview' and len(parts) == 5:
            stage = {'d': 'dimensions', 'r': 'render'}.get(parts[2])
            if not stage:
                raise ValueError('Неизвестный этап')
            v = self.db.version(uid, pid, stage, int(parts[3]))
            if not v['path']:
                raise ValueError('Изображение ещё не создано')
            self.app.store.navigate(uid, 'archive', pid)
            await self.app.say(message, uid, f"{LABELS[stage]} №{v['ordinal']} · сохранённый результат\n\n" + brief_text(v['plan'].brief),
                keyboard([[('Назад', f'history:{pid}:{int(parts[4])}')]]), v['path'])
            return True
        if action not in ('approve_sketch', 'dimensions', 'render', 'wfapprove', 'wfgen', 'wfretry', 'wfback', 'wfsketch') or len(parts) not in (3,4):
            return False
        number = int(parts[-1])
        if action.startswith('wf'):
            if len(parts) != 4 or {'d': 'dimensions', 'r': 'render'}.get(parts[2]) != self.db.state(uid, pid)['stage']:
                raise ValueError('Это кнопка другого этапа.')
        if self.app.lock(uid).locked() and action != 'wfback':
            await self.app.say(message, uid, 'Обработка уже идёт. Правки можно прислать сообщением.')
            return True
        if action == 'approve_sketch':
            self.db.approve_sketch(uid, pid, number)
            await self.app.result(message, uid, self.app.store.project(uid, pid), number)
        elif action in ('dimensions', 'render'):
            self.db.begin(uid, pid, action, number)
            await self.analyse(message, uid, pid)
        else:
            v = self.db.version(uid, pid)
            if v['number'] != number:
                raise ValueError('Это устаревшая кнопка этапа.')
            if action == 'wfapprove':
                self.db.approve(uid, pid, v['stage'], number)
                await self.show(message, uid, pid)
            elif action == 'wfgen':
                await self.generate(message, uid, pid, number)
            elif action == 'wfretry':
                await self.retry(message, uid, pid)
            elif action == 'wfback':
                self.db.back(uid, pid)
                await self.app.open_project(message, uid, self.app.store.project(uid, pid))
            elif action == 'wfsketch':
                messages = self.db.messages(uid, pid, v)
                if v['stage'] == 'render':
                    source = self.db.version(uid, pid, 'dimensions', v['source_number'])
                    messages = self.db.messages(uid, pid, source) + messages
                latest = next((m for m in reversed(messages) if m['role'] == 'user'), None)
                self.db.sketch(uid, pid)
                if latest:
                    image = None
                    if latest['path']:
                        from pathlib import Path
                        image = Path(latest['path']).read_bytes()
                    # Переносим пользовательские уточнения обоих этапов, а не
                    # только последнюю фразу «добавь полку», теряющую размеры.
                    text = 'Уточнения после утверждения эскиза, в порядке поступления:\n' + '\n'.join(
                        m['text'] for m in messages if m['role'] == 'user')
                    self.app.store.add_input(uid, pid, text, image, latest['mime'])
                    await self.app.analyse(message, uid, pid)
                else:
                    await self.app.open_project(message, uid, self.app.store.project(uid, pid))
        return True
