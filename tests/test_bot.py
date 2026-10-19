import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bot.main import Application
from bot.dialogue_client import DialogueError
from services.dialogue.src.rules import analysis_result
from bot.sketch_client import SketchError
from bot.store import Store
from tests.test_project import PNG, completed
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText


class FakeBot:
    def __init__(self):
        self.messages = {}
        self.answers, self.photos, self.events = [], [], []
        self.last_message_id = 0

    async def download(self, file_id, destination):
        destination.write(PNG)

    def sent(self, uid, text=None, photo=None, markup=None):
        result = FakeMessage(uid=uid, bot=self)
        result.from_user = SimpleNamespace(id=999, is_bot=True)
        result.text, result.caption = (None, text) if photo else (text, None)
        result.photo = [SimpleNamespace(file_id='image')] if photo else []
        result.reply_markup = markup
        self.messages[result.message_id] = result
        self.events.append(('send', result.message_id))
        self.answers.append((text, markup))
        if photo:
            self.photos.append((photo, text, markup))
        return result

    def get(self, mid):
        if mid not in self.messages:
            raise TelegramBadRequest(method=EditMessageText(chat_id=10, message_id=mid, text='x'),
                                     message='message to edit not found')
        return self.messages[mid]

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        message = self.get(message_id)
        assert not message.photo, 'Cannot edit photo with editMessageText'
        assert len(text.encode('utf-16-le')) // 2 <= 4096
        message.text, message.reply_markup = text, reply_markup
        self.answers.append((text, reply_markup))
        self.events.append(('text', message_id))
        return message

    async def edit_message_caption(self, chat_id, message_id, caption, reply_markup=None):
        message = self.get(message_id)
        assert message.photo, 'Caption requires photo'
        assert len(caption.encode('utf-16-le')) // 2 <= 1024
        message.caption, message.reply_markup = caption, reply_markup
        self.answers.append((caption, reply_markup))
        self.events.append(('caption', message_id))
        return message

    async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
        message = self.get(message_id)
        source = media.media
        content = Path(source.path).read_bytes() if hasattr(source, 'path') else source.data
        message.photo = [SimpleNamespace(file_id='image')]
        message.text, message.caption, message.reply_markup = None, media.caption, reply_markup
        assert len(media.caption.encode('utf-16-le')) // 2 <= 1024
        self.answers.append((media.caption, reply_markup))
        self.photos.append((content, media.caption, reply_markup))
        self.events.append(('media', message_id))
        return message


class FakeMessage:
    bots = {}

    def __init__(self, text="", uid=10, image=False, bot=None):
        self.text = text or None
        self.caption = text if image else None
        self.photo = []
        self.document = (SimpleNamespace(file_id="file", file_size=len(PNG), mime_type="image/png")
                         if image else None)
        self.chat = SimpleNamespace(type="private", id=uid)
        self.from_user = SimpleNamespace(id=uid)
        self.bot = bot or self.bots.setdefault(uid, FakeBot())
        self.bot.last_message_id += 1
        self.message_id = self.bot.last_message_id
        self.answers, self.photos = self.bot.answers, self.bot.photos

    async def answer(self, text, reply_markup=None):
        return self.bot.sent(self.chat.id, text, markup=reply_markup)

    async def answer_photo(self, photo, caption=None, reply_markup=None):
        return self.bot.sent(self.chat.id, caption, Path(photo.path).read_bytes(), reply_markup)


class FakeDialogue:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = None
        self.failed = False

    async def analyse(self, brief, messages, references):
        self.calls.append((brief, messages, references))
        self.started.set()
        if self.release and len(self.calls) == 1:
            await self.release.wait()
        if self.failed:
            raise DialogueError("test failure")
        last = next(m["text"] for m in reversed(messages) if m["role"] == "user")
        return analysis_result(completed("Тумба. " + last), messages)


class FakeSketch:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = None
        self.failed = False

    async def generate(self, request):
        self.calls.append(request)
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.failed:
            raise SketchError("test failure")
        return PNG, "test-model"


async def callback(app, message, data):
    uid = message.chat.id
    answers = []
    async def answer(text=None):
        answers.append(text)
    control = app.store.control(uid)
    if control:
        target = message.bot.messages.get(control['message_id'], message)
    else:
        target = await message.answer('Старое меню')
    await app.callback(SimpleNamespace(from_user=SimpleNamespace(id=uid),
        message=target, data=data, answer=answer))
    return answers


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeMessage.bots = {}
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.dialogue, self.sketch = FakeDialogue(), FakeSketch()
        self.app = Application(self.store, self.dialogue, self.sketch)

    async def prepare(self):
        message = FakeMessage("Тумба 1000 × 800 × 500 мм", image=True)
        await self.app.receive(message)
        project = self.store.project(10, self.store.session(10)["project_id"])
        return message, project

    async def test_end_to_end_sketch_confirmation_and_later_edit(self):
        message, project = await self.prepare()
        self.assertEqual(self.sketch.calls, [])
        await callback(self.app, message, f"generate:{project['id']}:{project['revision']}")
        self.assertEqual(len(self.sketch.calls), 1)
        self.assertEqual(message.photos[-1][0], PNG)
        edit = FakeMessage("Добавь полку")
        await self.app.receive(edit)
        revised = self.store.revision(10, project["id"])
        self.assertIn("Добавь полку", revised["brief"].description)
        self.assertFalse(revised["approved"])
        self.assertGreater(revised["number"], project["revision"])
        self.assertEqual(len(self.dialogue.calls[-1][2]), 2)  # исходный эскиз + результат

    async def test_double_confirmation_reuses_existing_image(self):
        message, project = await self.prepare()
        data = f"generate:{project['id']}:{project['revision']}"
        await callback(self.app, message, data)
        await callback(self.app, message, data)
        self.assertEqual(len(self.sketch.calls), 1)
        self.assertEqual(len([event for event in message.bot.events if event[0] == 'send']), 1)

    async def test_back_navigation_preserves_brief(self):
        message, project = await self.prepare()
        await callback(self.app, message, f"back:{project['id']}")
        self.assertEqual(self.store.session(10)["screen"], "menu")
        self.assertEqual(self.store.revision(10, project["id"])["brief"].width_mm, 1000)
        await self.app.receive(FakeMessage("Измени высоту"))
        self.assertEqual(len(self.store.projects(10)), 1)

    async def test_stale_callback_does_not_generate(self):
        message, project = await self.prepare()
        await self.app.receive(FakeMessage("Добавь полку"))
        await callback(self.app, message, f"generate:{project['id']}:{project['revision']}")
        self.assertEqual(len(self.sketch.calls), 0)
        self.assertIn("старой версии", message.answers[-1][0])

    async def test_failed_analysis_keeps_input_and_retry_works(self):
        self.dialogue.failed = True
        message, project = await self.prepare()
        self.assertFalse(self.store.revision(10, project["id"])["analysed"])
        self.assertEqual(self.store.messages(10, project["id"])[0]["role"], "user")
        self.dialogue.failed = False
        await callback(self.app, message, f"retry:{project['id']}")
        self.assertTrue(self.store.revision(10, project["id"])["analysed"])

    async def test_new_data_during_analysis_saved_immediately(self):
        self.dialogue.release = asyncio.Event()
        first = asyncio.create_task(self.app.receive(FakeMessage("Тумба", image=True)))
        await self.dialogue.started.wait()
        second = asyncio.create_task(self.app.receive(FakeMessage("Добавь полку")))
        await asyncio.sleep(0)
        pid = self.store.session(10)["project_id"]
        self.assertEqual(self.store.messages(10, pid)[-1]["text"], "Добавь полку")
        self.dialogue.release.set()
        await asyncio.gather(first, second)
        self.assertIn("Добавь полку", self.store.revision(10, pid)["brief"].description)
        self.assertEqual(len(self.dialogue.calls), 2)

    async def test_new_data_during_generation_does_not_attach_old_image_to_new_revision(self):
        message, project = await self.prepare()
        self.sketch.release = asyncio.Event()
        task = asyncio.create_task(callback(self.app, message,
                    f"generate:{project['id']}:{project['revision']}"))
        await self.sketch.started.wait()
        edit = asyncio.create_task(self.app.receive(FakeMessage("Добавь полку")))
        await asyncio.sleep(0)
        new_number = self.store.revision(10, project["id"])["number"]
        self.assertGreater(new_number, project["revision"])
        self.sketch.release.set()
        await asyncio.gather(task, edit)
        self.assertIsNone(self.store.artifact(10, project["id"], new_number))
        self.assertIsNotNone(self.store.artifact(10, project["id"], project["revision"]))

    async def test_menu_available_while_generation_runs(self):
        message, project = await self.prepare()
        self.sketch.release = asyncio.Event()
        task = asyncio.create_task(callback(self.app, message,
                    f"generate:{project['id']}:{project['revision']}"))
        await self.sketch.started.wait()
        await asyncio.wait_for(callback(self.app, message, "menu"), timeout=1)
        self.assertEqual(self.store.session(10)["screen"], "menu")
        self.sketch.release.set()
        await task
        self.assertEqual(self.store.session(10)["screen"], "menu")

    async def test_other_user_callback_is_rejected(self):
        message, project = await self.prepare()
        stranger = FakeMessage(uid=11)
        await callback(self.app, stranger, f"open:{project['id']}")
        self.assertIsNone(self.store.session(11)['project_id'])
        self.assertNotIn(project['id'], str(stranger.answers[-1][1]))

    async def test_archived_version_does_not_restore_or_replace_current(self):
        message, project = await self.prepare()
        await self.app.receive(FakeMessage("Добавь полку"))
        current = self.store.project(10, project["id"])["revision"]
        await callback(self.app, message, f"version:{project['id']}:{project['revision']}")
        self.assertEqual(self.store.project(10, project["id"])["revision"], current)

    async def test_restart_continues_existing_project(self):
        message, project = await self.prepare()
        restarted = Application(Store(self.temp.name), FakeDialogue(), FakeSketch())
        await restarted.receive(FakeMessage("Добавь полку"))
        self.assertEqual(restarted.store.session(10)["project_id"], project["id"])
        history = restarted.dialogue.calls[0][1]
        self.assertIn("Тумба 1000", history[0]["text"])
        self.assertEqual(history[-1]["text"], "Добавь полку")
