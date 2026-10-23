"""Interpret short replies only in the context of a specific shown question."""
import re
from .evidence import normalize


def gap_question(label, value):
    return (f'Для секции «{label}» сохранено {value:g} мм между полками. '
            'Это чистый просвет между полками или расстояние между их верхними поверхностями?')


QUESTION = re.compile(
    r'Для секции «(?P<label>[^»]+)» сохранено (?P<value>\d+(?:[.,]\d+)?) мм между полками\. '
    r'Это чистый просвет между полками или расстояние между их верхними поверхностями\?')


def explicit_basis(text):
    text = normalize(text)
    # A question, a denial or both alternatives is not a confirmed choice.
    if '?' in text or re.search(r'\b(?:не|нет|либо|или)\b', text):
        return None
    clear = bool(re.search(r'просвет|чист\w*\s+(?:расстоян|промежут|зазор)', text))
    pitch = bool(re.search(r'\bшаг\b|между\s+(?:их\s+)?(?:верхними|нижними)\s+(?:поверхност|плоскост)', text))
    return ('clear_gap' if clear else 'pitch') if clear != pitch else None


def answer_for(label, messages):
    """Latest explicit answer to this section's question; no number required."""
    questions = {}
    answer = None
    for message in messages:
        if message.role == 'assistant':
            _, separator, text = message.text.partition('Нужно уточнить:\n')
            questions = {m.group(1): m.group(2).strip() for m in
                         re.finditer(r'(?m)^\s*(\d+)[.)]\s*(.+)$', text)} if separator else {}
            continue
        if message.role != 'user' or not questions:
            continue
        numbered = list(re.finditer(r'(?m)^\s*(\d+)[.)]\s*(.+)$', message.text))
        pairs = [(questions.get(m.group(1), ''), m.group(2).strip()) for m in numbered]
        if not numbered and len(questions) == 1:
            pairs = [(next(iter(questions.values())), message.text.strip())]
        for question, quote in pairs:
            match = QUESTION.fullmatch(question)
            if not match or normalize(match['label']) != normalize(label):
                continue
            basis = explicit_basis(quote)
            if basis is None and normalize(quote).strip('.!') in ('первое', 'первый вариант', '1'):
                basis = 'clear_gap'
            elif basis is None and normalize(quote).strip('.!') in ('второе', 'второй вариант', '2'):
                basis = 'pitch'
            if basis and len(quote) <= 1500:
                answer = (basis, quote)
    return answer


def question_for_section(question, label):
    match = QUESTION.fullmatch(question.strip())
    return bool(match and normalize(match['label']) == normalize(label))
