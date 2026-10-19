# Сервис черно-белого эскиза

Одна задача: из актуального описания и референсов создать черно-белый эскиз.
Не общается с Telegram, не уточняет заявку, не наносит размеры, не считает раскрой.

Зависимости: `pip install -r services/sketch/requirements.txt`.
Запуск из корня: `python -m services.sketch.src.app`.
Нужны SKETCH_SERVICE_TOKEN и ключ выбранного API: OPENROUTER_API_KEY при
AI_PROVIDER=openrouter, OPENAI_API_KEY при openai. Сервис сам обращается
к выбранному API и не получает ключи через Telegram или заявку.

- GET /health — проверка процесса, без обращения к модели.
- POST /v1/sketches — заявка SketchRequest из contracts/models.py.
- Заголовок Authorization: Bearer с внутренним ключом.
- Результат — SketchResponse, изображение PNG в base64.

request_id обозначает одну версию заявки. Повторное использование с другими
данными возвращает 409. Готовый результат кэшируется на диске и доступен
после перезапуска. Одновременный повтор того же запроса не запускает второй
вызов модели. Ошибочную попытку можно повторить отдельным явным запросом.

Собственная база и изображения находятся в data/sketch.
Самостоятельная сборка: `docker build -f services/sketch/Dockerfile -t furniture-sketch .`.
Для переноса на отдельный сервер нужны только services/sketch, contracts,
infrastructure и run_sketch.py; код bot и токен Telegram не требуются.
