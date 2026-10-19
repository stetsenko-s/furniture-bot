"""Самостоятельный HTTP-сервис: без Telegram и доступа к базе проектов."""
import asyncio
import base64
import hashlib
import hmac
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from contracts.models import SketchRequest, SketchResponse, ReferenceImage
from infrastructure.env import load_env, require, data_dir
from infrastructure.ai import ai_settings
from .provider import ImageProvider, ProviderError

LOG = logging.getLogger(__name__)


class Cache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "requests.sqlite3"
        with self.connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS requests(
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL,
                path TEXT, model TEXT)""")
            # Прошлый процесс остановился: незавершенные запросы можно повторить явно.
            con.execute("UPDATE requests SET state='failed' WHERE state='running'")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=15)
        try:
            with con:
                yield con
        finally:
            con.close()

    def claim(self, key, digest):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT digest,state,path,model FROM requests WHERE id=?",
                              (key,)).fetchone()
            if row and row[0] != digest:
                return "conflict", None
            if row and row[1] in ("running", "complete"):
                return row[1], row[2:]
            con.execute("INSERT OR REPLACE INTO requests VALUES(?,?,'running',NULL,NULL)",
                        (key, digest))
            return "new", None

    def finish(self, key, image, model):
        path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".png")
        path.write_bytes(base64.b64decode(image.data_base64))
        with self.connect() as con:
            con.execute("UPDATE requests SET state='complete',path=?,model=? WHERE id=?",
                        (str(path), model, key))

    def fail(self, key):
        with self.connect() as con:
            con.execute("UPDATE requests SET state='failed' WHERE id=?", (key,))


def create_app(provider, directory, token):
    if not token:
        raise ValueError("Нужен SKETCH_SERVICE_TOKEN.")
    cache = Cache(directory)
    capacity = asyncio.Semaphore(2)
    app = web.Application(client_max_size=60 * 1024 * 1024)

    async def health(request):
        return web.json_response({"service": "sketch", "contract_version": 1, "status": "ok"})

    async def generate(request):
        authorization = request.headers.get("Authorization", "")
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = SketchRequest.model_validate(await request.json())
        except (ValidationError, ValueError):
            return web.json_response({"error": "invalid_request"}, status=422)
        digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        state, cached = cache.claim(payload.request_id, digest)
        if state in ("conflict", "running"):
            return web.json_response({"error": state}, status=409)
        if state == "complete":
            path, model = cached
            image = ReferenceImage(mime_type="image/png",
                data_base64=base64.b64encode(Path(path).read_bytes()).decode())
        else:
            try:
                async with capacity:
                    image, model = await provider.generate(payload)
                cache.finish(payload.request_id, image, model)
            except ProviderError:
                cache.fail(payload.request_id)
                LOG.warning("Генерация не завершена: %s", payload.request_id)
                return web.json_response({"error": "provider_failed"}, status=502)
            except BaseException:
                cache.fail(payload.request_id)
                raise
        result = SketchResponse(project_id=payload.project_id, revision=payload.revision,
                                image=image, model=model)
        return web.json_response(result.model_dump())

    app.router.add_get("/health", health)
    app.router.add_post("/v1/sketches", generate)
    return app


def main():
    load_env()
    require('SKETCH_SERVICE_TOKEN')
    ai = ai_settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    provider = ImageProvider(ai.key, ai.sketch_model,
        os.environ.get("SKETCH_QUALITY", "medium"),
        os.environ.get("SKETCH_SIZE", "1536x1024"), provider=ai.provider)
    web.run_app(create_app(provider, data_dir("sketch"), os.environ["SKETCH_SERVICE_TOKEN"]),
                host=os.environ.get("SKETCH_HOST", "127.0.0.1"),
                port=int(os.environ.get("SKETCH_PORT", "8091")),
                access_log=None)


if __name__ == "__main__":
    main()
