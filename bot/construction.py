"""Проверка полноты распознанной конструкции перед генерацией эскиза."""


def pending_questions(revision, has_sketch):
    questions = list(revision['questions'])
    brief = revision['brief']
    if not brief.description.strip():
        questions.append('Какое изделие нужно и из каких элементов оно состоит?')
    for field, label in (('width_mm', 'ширину'), ('height_mm', 'высоту'), ('depth_mm', 'глубину')):
        if getattr(brief, field) is None:
            questions.append(f'Укажите {label} изделия в миллиметрах.')
    if not has_sketch:
        questions.append('Пришлите фотографию рукописного эскиза.')
    return list(dict.fromkeys(questions))


def clarification_questions(result, messages):
    """Не позволяет пустому questions скрыть неизвестные двери/наполнение.

    Распознавание остаётся задачей модели. Здесь проверяется полнота её
    ответа, а для отсутствующих дверей — наличие цитаты пользователя.
    """
    questions = []
    covered = set()
    if result.is_cabinet:
        if not result.cabinet_sections:
            questions.append('Какие отделения есть у шкафа и какие из них закрываются дверями?')
        user_texts = [' '.join(m['text'].casefold().split())
                      for m in messages if m['role'] == 'user']
        for index, section in enumerate(result.cabinet_sections):
            quote = ' '.join((section.no_doors_user_quote or '').casefold().split())
            absence_confirmed = bool(quote) and any(quote in text for text in user_texts)
            if section.doors == 'unknown' or (section.doors == 'absent' and not absence_confirmed):
                questions.append(f'{section.name}: предусмотрены двери? Если да — сколько '
                                 'и какого типа: распашные или раздвижные?')
                covered.add(('doors', index))
            elif section.doors == 'present' and not section.door_details_known:
                questions.append(f'{section.name}: сколько дверей, как они открываются '
                                 'и какую часть шкафа закрывают?')
                covered.add(('doors', index))
            if not section.interior_known:
                questions.append(f'{section.name}: что находится внутри — полки, штанга, ящики '
                                 'или отделение пустое? Укажите количество и расположение элементов.')
                covered.add(('interior', index))
    for question in result.questions:
        # Подавляем только тему, уже покрытую вопросом по тому же отделению.
        # «Куда крепить ручки, если дверей нет?» — contradiction, не doors.
        # Неизвестная привязка и прочие вопросы сохраняются: догадка по словам
        # «дверь»/«ручка» могла бы скрыть важное противоречие.
        if (question.topic, question.section_index) not in covered:
            text = question.text.strip()
            if text:
                questions.append(text)
    return list(dict.fromkeys(questions))
