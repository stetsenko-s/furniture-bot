"""Проверка происхождения цитат и контекст коротких нумерованных ответов."""
import re


def normalize(text):
    text = ' '.join(text.casefold().replace('ё', 'е').split())
    # Разница между «150мм», «150 мм» и «150,5 мм» не меняет смысл цитаты.
    text = re.sub(r'(?<=\d),(?=\d)', '.', text)
    return re.sub(r'(?<=\d)\s+(?=мм\b)', '', text)


def user_quote(quote, messages):
    quote = normalize(quote or '')
    return bool(quote) and any(quote in normalize(m.text) for m in messages if m.role == 'user')


def numeric_quote(value, quote, messages):
    if not user_quote(quote, messages):
        return False
    numbers = [float(v) for v in re.findall(r'(?<!\w)\d+(?:\.\d+)?', normalize(quote))]
    return value is not None and value in numbers


def rejection_quote(quote, messages):
    # Наличие цитаты не доказывает её семантическую привязку к элементу:
    # эту привязку даёт агент, а здесь отсекаем выдуманные и числовые «отрицания».
    return user_quote(quote, messages) and bool(re.search(
        r'\bнет\b|\bтолько\b|\bне\s+(?:было|нуж|предусмотр|существ|отдел)|'
        r'\b(?:отсутств|лишн|ошиб|убер|удал|исключ)', normalize(quote)))


def numbered_answers(messages):
    """Только явная связь номера ответа с последним показанным списком."""
    questions = {}
    result = []
    for message in messages:
        if message.role == 'assistant':
            _, separator, text = message.text.partition('Нужно уточнить:\n')
            questions = {
                m.group(1): m.group(2).strip()
                for m in re.finditer(r'(?m)^\s*(\d+)[.)]\s*(.+)$', text)
            } if separator else {}
        elif message.role == 'user' and questions:
            for match in re.finditer(r'(?m)^\s*(\d+)[.)]\s*(.+)$', message.text):
                if match.group(1) in questions:
                    result.append({'question': questions[match.group(1)],
                                   'answer_quote': match.group(2).strip()})
    return result[-80:]


def absent_element(answer):
    """Отличаем отрицание элемента от «не знаю/нет размеров»."""
    text = normalize(answer)
    return bool(re.search(r'\bнет\b|\bотсутств\w*|\bне\s+(?:предусмотр\w*|существ\w*)', text)
                and re.search(r'\b(?:отделени|отдела|полк|штанг|вешалк|ящик|двер|секци|перегород|фасад)\w*', text)
                and not re.search(r'\bнет\s+(?:размер|данн|замер|значени|информац)\w*', text))


def rejected_dimensions(request):
    """Явное отрицание в ответе на показанный вопрос не зависит от решения LLM."""
    rejected = {}
    if request.plan:
        for pair in numbered_answers(request.messages):
            if absent_element(pair['answer_quote']):
                for item in request.plan.dimensions:
                    if (not item.id.startswith('overall_') and normalize(item.target) in normalize(pair['question'])
                            and denies_target(pair['answer_quote'], item.target)):
                        rejected[item.id] = pair['answer_quote']
            else:
                # Поздний ответ на тот же вопрос отменяет прежнее отрицание.
                for item in request.plan.dimensions:
                    if normalize(item.target) in normalize(pair['question']):
                        rejected.pop(item.id, None)
    return rejected


def denies_target(answer, target):
    """«Нет отделения» не отрицает ширину всей секции, даже при неверном номере."""
    text = normalize(answer)
    negation = re.search(r'\bнет\b|\bотсутств\w*|\bне\s+(?:предусмотр\w*|существ\w*)', text)
    if not negation:
        return False
    noun = r'\b(отдел|полк|штанг|вешалк|ящик|двер|секци|перегород|фасад)\w*'
    following = re.search(noun, text[negation.end():])
    preceding = list(re.finditer(noun, text[:negation.start()]))
    match = following or (preceding[-1] if preceding else None)
    if not match:
        return False
    root = match.group(1)
    return bool(re.search(r'\b' + root + r'\w*', normalize(target)))


def is_quantity(item):
    return bool(re.match(r'^(?:число|количество)\b', normalize(item.target)))


def analysis_context(request):
    """Старые неподтверждённые догадки не становятся исходными фактами."""
    data = request.model_dump(exclude={'references'})
    history = []
    for message in request.messages:
        if message.role == 'user':
            history.append(message.model_dump())
        else:
            _, separator, questions = message.text.partition('Нужно уточнить:\n')
            if separator:
                history.append({'role': 'assistant', 'text': 'Нужно уточнить:\n' + questions})
    data['messages'] = history[-80:]
    data['numbered_answers'] = numbered_answers(request.messages)
    denied = rejected_dimensions(request)
    data['rejected_elements'] = [{'id': d.id, 'target': d.target, 'source_quote': denied[d.id]}
                                 for d in request.plan.dimensions if d.id in denied] if request.plan else []
    if request.plan:
        clean = request.plan.model_copy(deep=True)
        clean.dimensions = [d for d in clean.dimensions if d.value_mm is not None
                            and not is_quantity(d) and d.id not in denied]
        clean.brief.description = request.brief.description
        clean.questions = []
        clean.reply = 'Ранее подтверждённые числовые значения; остальные размеры определите заново по исходным данным и ответам пользователя.'
        clean.inventory_complete = False
        clean.needs_sketch_changes = False
        data['plan'] = clean.model_dump()
    return data
