# Telegram-бот

Отвечает за проекты, переписку, уточнения, согласование и кнопки.

Запуск из корня проекта: `python run_bot.py`.
Зависимости: `pip install -r bot/requirements.txt`.
Нужны FURNITURE_BOT_TOKEN, SKETCH_SERVICE_TOKEN и ключ выбранного API:
OPENROUTER_API_KEY при AI_PROVIDER=openrouter или OPENAI_API_KEY при openai.
Генератор подключается через SKETCH_SERVICE_URL, а не прямым импортом.

main.py — обработчики и навигация; store.py — сохранение в SQLite;
llm.py — диалог через OpenRouter Chat Completions или OpenAI Responses API;
sketch_client.py — HTTP-клиент генератора.
panels.py — новая карточка ниже нового сообщения пользователя, редактирование
текущей карточки кнопками и обновлениями обработки, переключение изображения
и текста, страницы длинного описания. Идентификатор хранится в SQLite.

Собственная папка данных: data/bot. Не используйте базу сервиса sketch.
