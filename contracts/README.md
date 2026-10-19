# Контракты, версия 1

`models.py` — только форматы данных, без HTTP-клиентов, Telegram и баз данных.
Лишние поля отклоняются. Размеры — миллиметры; неизвестное значение — null.

- `Brief` — полное актуальное описание изделия, а не частичная правка.
- `ConversationMessage` — роль user/assistant и текст.
- `AnalysisRequest` — описание, история и референсы для сервиса dialogue.
- `AnalysisResult` — обновлённое описание, ответ и итоговые вопросы.
- `AnalysisResponse` — версия контракта и результат анализа.
- `ReferenceImage` — изображение в base64 с MIME-типом.
- `SketchRequest` — проект, ревизия, request_id, описание, референсы.
- `SketchResponse` — тот же проект и ревизия, PNG и использованная модель.

Внутренние схемы LLM (DialogueResult, CabinetSection, Clarification) находятся
в `services/dialogue/src/models.py`; бот не зависит от формата ответа нейросети.
При несовместимом изменении API повышается contract_version.
