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
