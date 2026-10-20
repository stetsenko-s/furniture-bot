"""Читаем только настройки этого проекта; секреты не выводим в журналы."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = {
    'AI_PROVIDER', 'OPENROUTER_API_KEY', 'OPENROUTER_DIALOGUE_MODEL', 'OPENROUTER_SKETCH_MODEL',
    "FURNITURE_BOT_TOKEN", "OPENAI_API_KEY", "DIALOGUE_MODEL", "SKETCH_MODEL",
    "SKETCH_QUALITY", "SKETCH_SIZE", "SKETCH_SERVICE_URL", "SKETCH_SERVICE_TOKEN",
    "SKETCH_HOST", "SKETCH_PORT", "DATA_DIR",
    "DIALOGUE_SERVICE_URL", "DIALOGUE_SERVICE_TOKEN", "DIALOGUE_HOST", "DIALOGUE_PORT",
}


def load_env(keys=None):
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in (KEYS if keys is None else keys):
            continue
        value = value.strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise SystemExit(f"Проверьте кавычки в настройке {key}.")
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


def require(*keys):
    missing = [key for key in keys if not os.environ.get(key, "").strip()]
    if missing:
        raise SystemExit("Заполните .env: " + ", ".join(missing))


def data_dir(component):
    return Path(os.environ.get("DATA_DIR", str(ROOT / "data"))) / component

KEYS.update(prefix + '_' + name for prefix in ('DIMENSIONS', 'RENDER')
            for name in ('AI_PROVIDER','API_KEY','TEXT_MODEL','IMAGE_MODEL','QUALITY','SIZE','SERVICE_TOKEN','SERVICE_URL','HOST','PORT'))
