"""Создаёт локальную конфигурацию, не заменяя существующую."""
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parent
    target = root / ".env"
    if target.exists():
        print(".env уже существует; настройки сохранены.")
        return
    template = (root / ".env.example").read_text(encoding="utf-8")
    template = template.replace("SKETCH_SERVICE_TOKEN=\n",
                                "SKETCH_SERVICE_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    target.write_text(template, encoding="utf-8")
    print("Создан .env. Впишите FURNITURE_BOT_TOKEN и OPENAI_API_KEY.")


if __name__ == "__main__":
    main()
