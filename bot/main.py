"""Telegram — постоянный диалог и управление проектом; не генератор картинки."""
# Прямой запуск файла из PyCharm передаём корневой точке входа.
# При обычном импорте и python -m bot.main этот блок не выполняется.
if __name__ == "__main__" and not __package__:
    import runpy
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    runpy.run_path(str(project_root / "run_bot.py"), run_name="__main__")
    raise SystemExit(0)

import asyncio
import base64
from io import BytesIO
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    BotCommand, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
)

from contracts.models import MAX_IMAGE_BYTES, ReferenceImage, SketchRequest
from infrastructure.env import load_env, require, data_dir
from .readiness import pending_questions
from .dialogue_client import DialogueClient, DialogueError
from .sketch_client import SketchClient, SketchError
from .store import Store
from .panels import Panels, PanelUnavailable, IN_CALLBACK

LOG = logging.getLogger(__name__)
DIMENSIONS_HINT = (
    'Размеры вводятся обычным сообщением в этот чат.\n'
    'Например: «Ширина 1000 мм, высота 800 мм, глубина 500 мм».\n'
    'Для изменения одного размера напишите, например: «Измени высоту на 900 мм».'
)


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows])


def navigation(project_id):
    return [("Назад", f"back:{project_id}")]


def project_keyboard(project, *, ready=False, retry=False, drawings=False):
    pid, number = project["id"], project["revision"]
    rows = []
    if ready:
        rows.append([("📐 Подтвердить и создать чертёж", f"generate:{pid}:{number}")])
    if retry:
        rows.append([("Попробовать ещё раз", f"retry:{pid}")])
    rows.append([("Данные изделия", f"summary:{pid}")])
    if drawings:
        rows.append([("Чертежи", f"history:{pid}")])
    rows.append([("Удалить", f"delete:{pid}"), ("Назад", f"back:{pid}"), ("Переименовать", f"rename:{pid}")])
    return keyboard(rows)


def reference_images(store, user_id, project_id):
    """Последние эскизы и предыдущий результат помогают сохранить конструкцию."""
    uploads = store.attachments(user_id, project_id)
    versions = store.drawings(user_id, project_id)
    previous = next((v for v in versions if v["path"]), None)
    selected = uploads[:3] if previous else uploads[:4]
    references = [ReferenceImage(mime_type=a["mime"],
        data_base64=base64.b64encode(Path(a["path"]).read_bytes()).decode())
        for a in reversed(selected)]
    if previous:
        references.append(ReferenceImage(mime_type="image/png",
            data_base64=base64.b64encode(Path(previous["path"]).read_bytes()).decode()))
    return references


def brief_text(brief):
    def number(value):
        return f"{value:g} мм" if value is not None else "не указано"
    return (
        f"{brief.title}\n\n{brief.description or 'Описание пока не заполнено.'}\n\n"
        f"Ширина: {number(brief.width_mm)}\n"
        f"Высота: {number(brief.height_mm)}\n"
        f"Глубина: {number(brief.depth_mm)}\n"
        f"Материал: {brief.material or 'пока не выбран'}\n"
        f"Цвет: {brief.color or 'пока не выбран'}\n"
        f"Фурнитура: {brief.hardware or 'пока не уточнена'}"
    )


class Application:
    def __init__(self, store, dialogue, sketch):
        self.store, self.dialogue, self.sketch = store, dialogue, sketch
        self.locks = {}
        self.panels = Panels(store)
        self.dp = Dispatcher()
        self.dp.message.register(self.command, Command("start", "menu", "help", "new", "project", "retry"))
        self.dp.callback_query.register(self.callback)
        self.dp.message.register(self.receive, F.chat.type == "private")

    def lock(self, user_id):
        return self.locks.setdefault(user_id, asyncio.Lock())

    async def say(self, message, uid, text, markup=None, image=None):
        if markup is None:
            pid = self.store.session(uid)['project_id']
            try:
                markup = self.project_markup(uid, self.store.project(uid, pid)) if pid else keyboard([[('Назад', 'menu')]])
            except LookupError:
                markup = keyboard([[('Назад', 'menu')]])
        await self.panels.show(message, uid, text, markup, image)

    def project_markup(self, uid, project, *, ready=False):
        revision = self.store.revision(uid, project['id'])
        versions = self.store.drawings(uid, project['id'])
        return project_keyboard(project, ready=ready and not revision['sketch_error'],
            retry=bool(revision['analysis_error'] or revision['sketch_error']),
            drawings=bool(versions))

    def visible(self, uid, pid):
        session = self.store.session(uid)
        return session['project_id'] == pid and session['screen'] in ('intake', 'review', 'result')

    async def menu(self, message, user_id):
        self.store.navigate(user_id, "menu")
        rows = [[("➕ Новый проект", "new")]]
        for project in self.store.projects(user_id):
            rows.append([(project["title"][:50], f"open:{project['id']}")])
        await self.say(message, user_id,
            "Мебельный проект: пришлите эскиз, уточним конструкцию и подготовим "
            "чёрно-белый чертёж.\n\nВыберите проект или создайте новый. "
            "Новые сообщения относятся к последнему открытому проекту. "
            "Для другого изделия нажмите «Новый проект».",
            keyboard(rows))

    async def intake(self, message, user_id, project):
        self.store.navigate(user_id, "intake", project["id"])
        await self.say(message, user_id, f"{project['title']}\n\n"
            "Пришлите эскиз фотографией или файлом JPG/PNG/WebP. "
            "Напишите, что нужно сделать. "
            "Материал, цвет и фурнитуру можно указать сейчас или уточнить позже.\n\n"
            f"{DIMENSIONS_HINT}\n\n"
            "Вы можете присылать дополнения обычными сообщениями в любой момент.",
            self.project_markup(user_id, project))

    async def summary(self, message, user_id, project, reply=None):
        revision = self.store.revision(user_id, project["id"])
        has_sketch = bool(self.store.attachments(user_id, project["id"]))
        questions = pending_questions(revision, has_sketch)
        ready = bool(revision["analysed"]) and not questions
        self.store.navigate(user_id, "review", project["id"])
        text = (reply + '\n\n' if reply else '') + brief_text(revision["brief"])
        if questions:
            text += "\n\nНужно уточнить:\n" + "\n".join(
                f"{i}. {q}" for i, q in enumerate(questions[:3], 1))
        elif not revision["analysed"]:
            text += "\n\nНовые данные сохранены. Отправьте уточнение, чтобы продолжить обработку."
        else:
            text += "\n\nПроверьте описание. Можно дописать правки или подтвердить генерацию."
        error = revision['analysis_error'] or revision['sketch_error']
        if error:
            text += '\n\n' + error
        text += '\n\n' + DIMENSIONS_HINT
        await self.say(message, user_id, text, self.project_markup(user_id, project, ready=ready))

    async def result(self, message, user_id, project, number=None):
        artifact = self.store.artifact(user_id, project["id"], number)
        if not artifact:
            await self.summary(message, user_id, project)
            return
        revision = self.store.revision(user_id, project['id'])
        brief = revision['brief']
        text = brief_text(brief) + f"\n\nЭскиз №{artifact['sketch_number']} — чёрно-белый, без размеров.\n" + (
            "Напишите, что изменить, или пришлите дополнительный эскиз. "
            "Размерные линии и производственная деталировка — следующие этапы.")
        if artifact['revision'] != project['revision']:
            text += '\n\nЭто последний сохранённый чертёж. Новые уточнения ещё не отражены на нём.'
        error = revision['analysis_error'] or revision['sketch_error']
        if error:
            text += '\n\n' + error
        text += '\n\n' + DIMENSIONS_HINT
        self.store.navigate(user_id, 'result', project['id'])
        await self.say(message, user_id, text, self.project_markup(user_id, project), artifact['path'])

    async def open_project(self, message, uid, project):
        versions = self.store.drawings(uid, project['id'])
        latest = next((v for v in versions if v['path']), None)
        if latest:
            await self.result(message, uid, project, latest['number'])
        elif self.store.revision(uid, project['id'])['analysed'] or self.store.messages(uid, project['id']):
            await self.summary(message, uid, project)
        else:
            await self.intake(message, uid, project)

    async def command(self, message):
        if message.chat.type != "private":
            return
        uid = message.from_user.id
        command = message.text.split()[0].split("@")[0]
        if command == "/new":
            await self.intake(message, uid, self.store.create(uid))
        elif command in ("/project", "/retry"):
            session = self.store.session(uid)
            if not session["project_id"]:
                await self.menu(message, uid)
                return
            if command == "/retry":
                await self.retry(message, uid, session['project_id'])
            else:
                await self.summary(message, uid, self.store.project(uid, session["project_id"]))
        else:
            # Явная команда должна показать меню в конце переписки:
            # редактирование прежней карточки не прокручивает чат к ней.
            # Кнопки menu/back продолжают редактировать ту же карточку.
            self.store.forget_control(uid)
            await self.menu(message, uid)

    async def analyse(self, message, uid, pid):
        async with self.lock(uid):
            try:
                revision = self.store.revision(uid, pid)
                if revision['analysed']:
                    if self.visible(uid, pid):
                        await self.summary(message, uid, self.store.project(uid, pid))
                    return
                if self.visible(uid, pid):
                    project = self.store.project(uid, pid)
                    await self.say(message, uid, f"{project['title']}\n\nДанные сохранены. Разбираю эскиз и уточнения…",
                                   self.project_markup(uid, project))
                while True:
                    revision = self.store.revision(uid, pid)
                    number = revision['number']
                    try:
                        result = await self.dialogue.analyse(revision['brief'],
                            self.store.messages(uid, pid), reference_images(self.store, uid, pid))
                    except DialogueError as exc:
                        # После удаления не создаём ни записей, ни сообщений.
                        project = self.store.project(uid, pid)
                        if project['revision'] != number:
                            continue
                        self.store.set_error(uid, pid, number, 'analysis', str(exc))
                        if self.visible(uid, pid):
                            await self.summary(message, uid, project)
                        return
                    if self.store.project(uid, pid)['revision'] != number:
                        continue
                    self.store.save_analysis(uid, pid, number, result)
                    if self.visible(uid, pid):
                        await self.summary(message, uid, self.store.project(uid, pid), result.reply)
                    return
            except LookupError:
                return  # проект удалён во время запроса/ожидания блокировки

    async def retry(self, message, uid, pid):
        revision = self.store.revision(uid, pid)
        if revision['sketch_error']:
            await self.generate(message, uid, pid, revision['number'])
        else:
            await self.analyse(message, uid, pid)

    async def receive(self, message):
        uid = message.from_user.id
        session = self.store.session(uid)
        if session['screen'] == 'rename' and session['project_id']:
            pid = session['project_id']
            if not message.text or message.text.startswith('/'):
                if message.text and message.text.startswith('/'):
                    await self.command(message)
                else:
                    await self.say(message, uid, 'Напишите новое название текстом в одной строке.',
                                   keyboard([[('Назад', f'cancel:{pid}')]]))
                return
            try:
                project = self.store.rename(uid, pid, message.text)
            except ValueError as exc:
                await self.say(message, uid, str(exc), keyboard([[('Назад', f'cancel:{pid}')]]))
                return
            await self.open_project(message, uid, project)
            return
        image, mime = None, None
        if message.photo:
            file = message.photo[-1]
            mime = "image/jpeg"
        elif message.document:
            file = message.document
            mime = file.mime_type
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                await self.say(message, uid, "Для эскиза пришлите JPG, PNG или WebP. "
                          "Описание и размеры можно написать текстом.")
                return
        else:
            file = None
        text = (message.text or message.caption or "").strip()
        if file:
            if file.file_size and file.file_size > MAX_IMAGE_BYTES:
                await self.say(message, uid, "Эскиз должен быть не больше 10 МБ.")
                return
            destination = BytesIO()
            try:
                await message.bot.download(file.file_id, destination=destination)
                image = destination.getvalue()
                ReferenceImage(mime_type=mime, data_base64=base64.b64encode(image).decode())
            except Exception as exc:
                # Не выводим URL Telegram: он содержит токен бота.
                LOG.warning("Не удалось принять изображение (%s)", type(exc).__name__)
                await self.say(message, uid, "Не удалось прочитать изображение. Попробуйте отправить его ещё раз.")
                return
            text = text or "Добавлен эскиз. Уточни, что на нём изображено."
        if not text:
            await self.say(message, uid, "Пришлите текст или фотографию эскиза. Голосовые сообщения "
                      "пока не поддерживаются.")
            return
        if text.startswith("/"):
            await self.say(message, uid, "Доступны /menu, /new, /project и /retry.")
            return
        if len(text) > 12000:
            await self.say(message, uid, "Разделите описание на несколько сообщений.")
            return
        session = self.store.session(uid)
        project = (self.store.project(uid, session["project_id"])
                   if session["project_id"] else self.store.create(uid))
        # Важно: запись в БД ДО ожидания блокировки ИИ. Сообщение не пропадает,
        # даже если в это время создаётся картинка или анализируется прошлый ввод.
        self.store.add_input(uid, project["id"], text, image, mime)
        if self.lock(uid).locked():
            await self.say(message, uid, 'Дополнение сохранено. Учту его после текущей обработки.',
                           self.project_markup(uid, self.store.project(uid, project['id'])))
        await self.analyse(message, uid, project["id"])

    async def generate(self, message, uid, pid, number):
        async with self.lock(uid):
            try:
                self.store.approve(uid, pid, number)
                existing = self.store.artifact(uid, pid, number)
                if existing:
                    await self.result(message, uid, self.store.project(uid, pid), number)
                    return
                request = SketchRequest(request_id=f'{pid}-v{number}', project_id=pid,
                    revision=number, brief=self.store.generation_brief(uid, pid, number),
                    references=reference_images(self.store, uid, pid))
                project = self.store.project(uid, pid)
                if self.visible(uid, pid):
                    await self.say(message, uid, f"{project['title']}\n\nСоздаю эскиз. "
                        'Это может занять несколько минут. Уточнения можно присылать во время обработки.',
                        self.project_markup(uid, project))
                try:
                    image, model = await self.sketch.generate(request)
                except SketchError as exc:
                    project = self.store.project(uid, pid)
                    self.store.set_error(uid, pid, number, 'sketch', str(exc))
                    if self.visible(uid, pid):
                        await self.summary(message, uid, project)
                    return
                self.store.save_artifact(uid, pid, number, image, model)
                project = self.store.project(uid, pid)
                if project['revision'] == number and self.visible(uid, pid):
                    await self.result(message, uid, project, number)
            except LookupError:
                return  # поздний результат удалённого проекта не сохраняется

    async def callback(self, callback: CallbackQuery):
        uid = callback.from_user.id
        message = callback.message
        if not message or message.chat.type != "private":
            await callback.answer()
            return
        control = self.store.control(uid)
        if control and (control['chat_id'], control['message_id']) != (message.chat.id, message.message_id):
            await callback.answer('Это старое меню. Откройте актуальное командой /menu.')
            return
        if not control:
            self.store.save_control(uid, message.chat.id, message.message_id, bool(message.photo), {})
        await callback.answer()
        context = IN_CALLBACK.set(True)
        parts = (callback.data or "").split(":")
        action = parts[0]
        try:
            if action == 'page' and len(parts) == 2:
                await self.panels.page(message, uid, int(parts[1]))
                return
            if action == "menu":
                await self.menu(message, uid)
                return
            if action == "new":
                await self.intake(message, uid, self.store.create(uid))
                return
            if len(parts) < 2:
                return
            pid = parts[1]
            project = self.store.project(uid, pid)
            if action == "open":
                await self.open_project(message, uid, project)
                return
            if self.store.session(uid)["project_id"] != pid:
                await self.say(message, uid, "Эта кнопка относится к другому проекту. "
                          "Сначала выберите его в меню.", keyboard([[("🏠 В меню", "menu")]]))
                return
            if action == 'back':
                if self.store.session(uid)['screen'] in ('history', 'archive', 'rename', 'delete'):
                    await self.open_project(message, uid, project)
                else:
                    await self.menu(message, uid)
            elif action == 'cancel':
                await self.open_project(message, uid, project)
            elif action == 'rename':
                self.store.navigate(uid, 'rename', pid)
                await self.say(message, uid, f"Текущее название: {project['title']}\n\n"
                    'Напишите новое название — от 1 до 100 символов в одной строке.',
                    keyboard([[('Назад', f'cancel:{pid}')]]))
            elif action == 'delete':
                self.store.navigate(uid, 'delete', pid)
                await self.say(message, uid, f"Удалить «{project['title']}»?\n\n"
                    'Данные проекта, переписка и сохранённые версии чертежей будут удалены из бота. Это действие нельзя отменить.',
                    keyboard([[('Удалить', f'confirm_delete:{pid}'), ('Отмена', f'cancel:{pid}')]]))
            elif action == 'confirm_delete':
                if self.store.session(uid)['screen'] != 'delete':
                    raise ValueError('Сначала подтвердите удаление в карточке проекта.')
                self.store.delete(uid, pid)
                await self.menu(message, uid)
            elif action == "summary":
                await self.summary(message, uid, project)
            elif action == "retry":
                if self.lock(uid).locked():
                    await callback.answer('Обработка уже идёт. Уточнения можно прислать сообщением.')
                    return
                await self.retry(message, uid, pid)
            elif action in ('history', 'drawing'):
                page = int(parts[2]) if len(parts) == 3 else 0
                if page < 0:
                    raise ValueError('Некорректная страница списка чертежей.')
                self.store.navigate(uid, "history", pid)
                drawings = self.store.drawings(uid, pid, limit=21, offset=page * 20)
                rows = [[(f"Эскиз №{v['sketch_number']}",
                          f"version:{pid}:{v['number']}:{page}")] for v in drawings[:20]]
                pages = []
                if page:
                    pages.append(('← Предыдущая страница', f'history:{pid}:{page-1}'))
                if len(drawings) > 20:
                    pages.append(('Далее →', f'history:{pid}:{page+1}'))
                if pages:
                    rows.append(pages)
                rows.append(navigation(pid))
                text = ('Чертежи проекта — от новых к старым.\n'
                        'Здесь доступны все сохранённые рисунки, включая последний. '
                        'Выберите рисунок для просмотра. Текущие данные проекта не изменятся.')
                if not drawings:
                    text = 'На этой странице пока нет сохранённых чертежей.'
                await self.say(message, uid, text, keyboard(rows))
            elif action == "version" and len(parts) in (3, 4):
                number = int(parts[2])
                page = int(parts[3]) if len(parts) == 4 else 0
                if page < 0:
                    raise ValueError('Некорректная страница списка чертежей.')
                revision = self.store.revision(uid, pid, number)
                self.store.navigate(uid, 'archive', pid)
                artifact = self.store.artifact(uid, pid, number)
                if artifact is None:
                    raise ValueError('Этот эскиз ещё не создан.')
                await self.say(message, uid, f"Эскиз №{artifact['sketch_number']} · сохранённый результат\n\n" + brief_text(revision['brief']),
                    keyboard([[('Назад', f'history:{pid}:{page}')]]), artifact['path'] if artifact else None)
            elif action == "generate" and len(parts) == 3:
                if self.lock(uid).locked():
                    await callback.answer('Обработка уже идёт. Дополнения можно прислать сообщением.')
                    return
                await self.generate(message, uid, pid, int(parts[2]))
        except PanelUnavailable:
            await callback.answer('Карточка недоступна. Отправьте /menu, чтобы открыть её заново.')
        except LookupError:
            await self.menu(message, uid)
        except ValueError as exc:
            await self.say(message, uid, str(exc), keyboard([[('Назад', 'menu')]]))
        except Exception as exc:
            LOG.error("Ошибка обработки (%s)", type(exc).__name__)
            await callback.answer('Не удалось завершить действие. Откройте /menu и повторите.')
        finally:
            IN_CALLBACK.reset(context)


async def main():
    load_env({'FURNITURE_BOT_TOKEN', 'SKETCH_SERVICE_TOKEN', 'SKETCH_SERVICE_URL',
              'DIALOGUE_SERVICE_TOKEN', 'DIALOGUE_SERVICE_URL', 'DATA_DIR'})
    require('FURNITURE_BOT_TOKEN', 'SKETCH_SERVICE_TOKEN', 'DIALOGUE_SERVICE_TOKEN')
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    application = Application(Store(data_dir("bot")),
        DialogueClient(os.environ.get("DIALOGUE_SERVICE_URL", "http://127.0.0.1:8092"),
                       os.environ["DIALOGUE_SERVICE_TOKEN"]),
        SketchClient(os.environ.get("SKETCH_SERVICE_URL", "http://127.0.0.1:8091"),
                     os.environ["SKETCH_SERVICE_TOKEN"]))
    async with Bot(os.environ["FURNITURE_BOT_TOKEN"]) as bot:
        await bot.set_my_commands([
            BotCommand(command="menu", description="Проекты и главное меню"),
            BotCommand(command="new", description="Новое изделие"),
            BotCommand(command="project", description="Данные текущего изделия"),
            BotCommand(command="retry", description="Повторить анализ сохранённых данных"),
        ])
        await application.dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
