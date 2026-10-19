"""Проверка реальных точек входа без токенов, запросов и запуска polling."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EntrypointTests(unittest.TestCase):
    def run_entrypoint(self, args, cwd):
        environment = dict(os.environ)
        # setdefault в загрузчике .env не заменяет пустые переменные:
        # ни локальный ключ, ни пользовательский токен не будут использованы.
        for key in ("FURNITURE_BOT_TOKEN", "OPENAI_API_KEY", "SKETCH_SERVICE_TOKEN"):
            environment[key] = ""
        result = subprocess.run([sys.executable, *args], cwd=cwd, env=environment,
                                capture_output=True, text=True, timeout=20)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ImportError", output)
        self.assertNotIn("ModuleNotFoundError", output)
        self.assertIn("FURNITURE_BOT_TOKEN", output)

    def test_direct_main_from_unrelated_working_directory(self):
        with TemporaryDirectory() as directory:
            self.run_entrypoint([str(ROOT / "bot" / "main.py")], directory)

    def test_root_launcher_from_unrelated_working_directory(self):
        with TemporaryDirectory() as directory:
            self.run_entrypoint([str(ROOT / "run_bot.py")], directory)

    def test_package_module_from_project_root(self):
        self.run_entrypoint(["-m", "bot.main"], ROOT)
