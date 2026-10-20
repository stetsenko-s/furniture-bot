"""Создаёт локальную конфигурацию, не заменяя существующую."""
from pathlib import Path
import re
import secrets


def configure(root):
    """Добавляет недостающие внутренние ключи, сохраняя ключи пользователя."""
    target = root / ".env"
    existing = target.exists()
    text = target.read_text(encoding='utf-8-sig') if existing else (root / '.env.example').read_text(encoding='utf-8')
    original = text
    for key in ('SKETCH_SERVICE_TOKEN', 'DIALOGUE_SERVICE_TOKEN', 'DIMENSIONS_SERVICE_TOKEN', 'RENDER_SERVICE_TOKEN'):
        pattern = re.compile(r'^' + key + r'\s*=(.*)$', re.MULTILINE)
        match = pattern.search(text)
        if match and match[1].split(' #', 1)[0].strip().strip('\"\''):
            continue
        line = key + '=' + secrets.token_urlsafe(32)
        text = pattern.sub(lambda _: line, text) if match else text.rstrip() + '\n' + line + '\n'
    if not existing or text != original:
        target.write_text(text, encoding='utf-8')
    print('Настройки подготовлены; существующие ключи сохранены.' if existing else
          'Создан .env. Заполните FURNITURE_BOT_TOKEN и ключ выбранного AI_PROVIDER.')


def main():
    configure(Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
