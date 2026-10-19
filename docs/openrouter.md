# Подключение OpenRouter

OpenRouter — один API-ключ и баланс для обращений к выбранным моделям.
Бот, анализатор dialogue и генератор sketch работают отдельными процессами.
Ключ API нужен только анализатору и генератору.

1. Зарегистрируйтесь на https://openrouter.ai/.
2. Пополните баланс: https://openrouter.ai/settings/credits.
   Итоговая сумма и комиссия показываются в форме оплаты. $5–10 — бюджет
   проверки, а не гарантия определённого количества изображений.
3. Создайте ключ на https://openrouter.ai/settings/keys.
4. В локальный `.env` добавьте строки:

```dotenv
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=вставьте_свой_ключ
OPENROUTER_DIALOGUE_MODEL=openai/gpt-4.1-mini
OPENROUTER_SKETCH_MODEL=openai/gpt-image-2
```

Существующие FURNITURE_BOT_TOKEN и SKETCH_SERVICE_TOKEN сохраняются.
OPENAI_API_KEY в этом режиме не используется. В чат ключ отправлять не нужно.
Если AI_PROVIDER отсутствует, старый `.env` продолжает использовать прямой OpenAI.

5. При обновлении выполните `python configure.py`. Перезапустите процессы:
   run_dialogue.py, run_sketch.py и run_bot.py.
6. /start → новый проект → эскиз → размеры → уточнения → подтверждение.
7. Расходы первых запросов смотрите на https://openrouter.ai/activity.

Для диалога используется Chat Completions с изображениями и строгой JSON-схемой.
Генератор использует отдельный Image API с исходным эскизом и предыдущим
результатом в input_references. Запрашивает одну PNG-картинку; качество и размер
управляются существующими SKETCH_QUALITY и SKETCH_SIZE.

Модели можно менять независимо, но диалогу нужны чтение изображений и JSON-схема,
а генератору — Image API, изображения на входе и PNG на выходе. Произвольная
текстовая или image-only модель может не подходить для редактирования эскиза.

Ошибка HTTP 401 означает проблему ключа, 402 обычно означает недостаточный
баланс, 429 — ограничение частоты. Бот сохраняет ввод и предлагает явный повтор.
Клиенты не делают автоматических повторов запросов; готовая версия кэшируется.

Смена провайдера не удаляет проекты. Уже готовые чертежи остаются в кэше;
для проверки новой модели создайте новую версию изделия или отдельный проект.

Документация:
- [Изображения на входе](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding)
- [JSON-схема ответа](https://openrouter.ai/docs/guides/features/structured-outputs)
- [Генерация по референсам](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)
- [Баланс и комиссия](https://openrouter.ai/docs/faq)
