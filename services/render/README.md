# render

Самостоятельный HTTP-сервис, без Telegram и доступа к базе проектов.
Запуск: `python run_render.py`. Настройка: префикс `RENDER_` в `.env`.
Промпты: `prompts/analysis.md` и `prompts/image.md`.
API: GET `/health`, POST `/v1/analyses`, POST `/v1/images`.
Контракты: `contracts/workflow.py`; авторизация Bearer `RENDER_SERVICE_TOKEN`.
SQLite и файлы результата принадлежат только этому сервису: `data/render`.
Dockerfile собирает сервис независимо от остальных компонентов.
См. общую архитектуру и сценарий проверки в docs.
