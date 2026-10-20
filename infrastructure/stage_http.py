"""HTTP-обвязка этапа: авторизация, контракты, независимый кэш изображений."""
import asyncio
import base64
import hashlib
import hmac
import sqlite3
from contextlib import closing
from pathlib import Path
from aiohttp import web
from pydantic import ValidationError
from contracts.models import ReferenceImage
from contracts.workflow import StageAnalysisRequest, StageAnalysisResponse, StageImageRequest, StageImageResponse
from .stage_ai import AIError


def create_stage_app(stage, provider, directory, token):
    if not token:
        raise ValueError('Нужен внутренний ключ сервиса')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / 'requests.sqlite3'
    with closing(sqlite3.connect(db)) as con, con:
        con.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, path TEXT, model TEXT)')
        con.execute("UPDATE requests SET state='failed' WHERE state='running'")
    capacity = asyncio.Semaphore(2)
    app = web.Application(client_max_size=60 * 1024 * 1024)

    def authorized(request):
        return hmac.compare_digest(request.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode())

    async def health(request):
        return web.json_response({'service': stage, 'contract_version': 1, 'status': 'ok'})

    async def analyse(request):
        if not authorized(request):
            return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            payload = StageAnalysisRequest.model_validate(await request.json())
            if payload.stage != stage:
                raise ValueError('Wrong stage')
        except (ValidationError, ValueError):
            return web.json_response({'error': 'invalid_request'}, status=422)
        try:
            plan = await provider.analyse(payload)
        except AIError:
            return web.json_response({'error': 'provider_failed'}, status=502)
        return web.json_response(StageAnalysisResponse(stage=stage, plan=plan).model_dump())

    async def generate(request):
        if not authorized(request):
            return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            payload = StageImageRequest.model_validate(await request.json())
            if payload.stage != stage:
                raise ValueError('Wrong stage')
            provider.validate_generation(payload.plan)
        except (ValidationError, ValueError):
            return web.json_response({'error': 'invalid_request'}, status=422)
        digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        with closing(sqlite3.connect(db)) as con, con:
            con.execute('BEGIN IMMEDIATE')
            cached = con.execute('SELECT digest,state,path,model FROM requests WHERE id=?', (payload.request_id,)).fetchone()
            if cached and (cached[0] != digest or cached[1] == 'running'):
                return web.json_response({'error': 'conflict'}, status=409)
            if not cached or cached[1] != 'complete':
                con.execute("INSERT OR REPLACE INTO requests VALUES(?,?,'running',NULL,NULL)", (payload.request_id, digest))

        def fail():
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("UPDATE requests SET state='failed' WHERE id=?", (payload.request_id,))

        if cached and cached[1] == 'complete':
            try:
                image = ReferenceImage(mime_type='image/png', data_base64=base64.b64encode(Path(cached[2]).read_bytes()).decode())
                model = cached[3]
            except (OSError, ValueError):
                fail()
                return web.json_response({'error': 'cached_image_missing'}, status=410)
        else:
            try:
                async with capacity:
                    image, model = await provider.generate(payload)
                path = directory / (hashlib.sha256(payload.request_id.encode()).hexdigest() + '.png')
                path.write_bytes(base64.b64decode(image.data_base64))
                with closing(sqlite3.connect(db)) as con, con:
                    con.execute("UPDATE requests SET state='complete',path=?,model=? WHERE id=?", (str(path), model, payload.request_id))
            except AIError:
                fail()
                return web.json_response({'error': 'provider_failed'}, status=502)
            except BaseException:
                fail()
                raise
        return web.json_response(StageImageResponse(stage=stage, project_id=payload.project_id,
            revision=payload.revision, image=image, model=model).model_dump())

    app.router.add_get('/health', health)
    app.router.add_post('/v1/analyses', analyse)
    app.router.add_post('/v1/images', generate)
    return app
