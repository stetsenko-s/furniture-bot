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
