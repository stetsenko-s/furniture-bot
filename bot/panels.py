"""Ответ ниже нового сообщения; кнопки и обработка обновляют текущую карточку."""
import asyncio
from contextvars import ContextVar
import struct
import zlib

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, FSInputFile, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton


IN_CALLBACK = ContextVar('in_callback', default=False)


class PanelUnavailable(Exception):
    pass


def neutral_banner():
    # Telegram не позволяет превратить фото обратно в текст. При переходе
    # в меню вместо чужого/старого чертежа показываем нейтральную узкую полосу.
    width, height = 640, 48
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    pixels = (b'\x00' + b'\x22\x2d\x38' * width) * height
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


BANNER = neutral_banner()


class Panels:
    def __init__(self, store):
        self.store = store
        self.locks = {}

    async def show(self, message, uid, text, markup=None, image=None, page=0):
        async with self.locks.setdefault(uid, asyncio.Lock()):
            control = self.store.control(uid)
            # Telegram не прокручивает чат при редактировании старого ответа.
            # Новый ввод получает карточку ниже себя, а следующие обновления
            # того же ввода (прогресс/результат) редактируют её. Проверка под
            # блокировкой не даёт позднему ответу создать лишнюю карточку.
            if (control and not IN_CALLBACK.get()
                    and not getattr(message.from_user, 'is_bot', False)
                    and message.chat.id == control['chat_id']
                    and message.message_id > control['message_id']):
                control = None
            media = bool(image) or bool(control and control['media'])
            limit = 850 if media else 3800  # запас на номер страницы и UTF-16 emoji
            pages, current = [], ''
            length = 0
            for char in text:
                size = len(char.encode('utf-16-le')) // 2
                if length + size > limit:
                    pages.append(current)
                    current, length = '', 0
                current += char
                length += size
            pages.append(current or ' ')
            page = max(0, min(page, len(pages) - 1))
            body = pages[page]
            payload = {'text': text, 'markup': markup.model_dump(mode='json') if markup else None,
                       'image': str(image) if image else None, 'page': page}
            if len(pages) > 1:
                body += f'\n\nСтраница {page + 1} из {len(pages)}'
                buttons = []
                if page:
                    buttons.append(InlineKeyboardButton(text='◀', callback_data=f'page:{page - 1}'))
                if page + 1 < len(pages):
                    buttons.append(InlineKeyboardButton(text='▶', callback_data=f'page:{page + 1}'))
                markup = InlineKeyboardMarkup(inline_keyboard=[buttons] + (markup.inline_keyboard if markup else []))
            try:
                if control:
                    args = dict(chat_id=control['chat_id'], message_id=control['message_id'], reply_markup=markup)
                    previous_image = control['payload'].get('image')
                    if media and (not control['media'] or previous_image != payload['image']):
                        photo = FSInputFile(image) if image else BufferedInputFile(BANNER, filename='menu.png')
                        await message.bot.edit_message_media(media=InputMediaPhoto(media=photo, caption=body), **args)
                    elif media:
                        await message.bot.edit_message_caption(caption=body, **args)
                    else:
                        await message.bot.edit_message_text(text=body, **args)
                    self.store.save_control(uid, control['chat_id'], control['message_id'], media, payload)
                    return
                if IN_CALLBACK.get():
                    raise PanelUnavailable()
                if image:
                    sent = await message.answer_photo(FSInputFile(image), caption=body, reply_markup=markup)
                else:
                    sent = await message.answer(body, reply_markup=markup)
                self.store.save_control(uid, sent.chat.id, sent.message_id, media, payload)
            except TelegramBadRequest as exc:
                # Не выводим URL/токен или полный ответ Telegram в журнал.
                if 'message is not modified' in exc.message.lower() and control:
                    self.store.save_control(uid, control['chat_id'], control['message_id'], media, payload)
                    return
                if any(reason in exc.message.lower() for reason in ('message to edit not found', "message can't be edited")):
                    self.store.forget_control(uid)
                    if IN_CALLBACK.get():
                        raise PanelUnavailable() from None
                    # Пользователь удалил карточку: новое обычное сообщение
                    # или /menu может создать её заново, кнопка — никогда.
                    sent = (await message.answer_photo(FSInputFile(image), caption=body, reply_markup=markup)
                            if image else await message.answer(body, reply_markup=markup))
                    self.store.save_control(uid, sent.chat.id, sent.message_id, bool(image), payload)
                    return
                raise

    async def page(self, message, uid, number):
        control = self.store.control(uid)
        if not control or not control['payload'].get('text'):
            return
        payload = control['payload']
        markup = InlineKeyboardMarkup.model_validate(payload['markup']) if payload['markup'] else None
        await self.show(message, uid, payload['text'], markup, payload['image'], number)
