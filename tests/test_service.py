import asyncio
import base64
from tempfile import TemporaryDirectory
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.llm import DialogueClient, DialogueError
from bot.sketch_client import SketchClient
from contracts.models import ReferenceImage, SketchRequest
from services.sketch.src.app import create_app
from services.sketch.src.provider import ImageProvider, ProviderError
from tests.test_project import PNG, completed


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.failed = False
        self.started = asyncio.Event()
        self.release = None

    async def generate(self, request):
        self.calls += 1
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.failed:
            raise ProviderError("test")
        return ReferenceImage(mime_type="image/png",
                              data_base64=base64.b64encode(PNG).decode()), "test-model"


def request_data():
    return SketchRequest(request_id="test-v2", project_id="a" * 32, revision=2,
        brief=completed().brief, references=[ReferenceImage(
            mime_type="image/png", data_base64=base64.b64encode(PNG).decode())])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.provider = FakeProvider()
        self.client = TestClient(TestServer(create_app(self.provider, self.temp.name, "secret")))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addCleanup(self.temp.cleanup)
        self.headers = {"Authorization": "Bearer secret"}

    async def post(self, payload=None, headers=None):
        return await self.client.post("/v1/sketches",
            json=payload if payload is not None else request_data().model_dump(),
            headers=self.headers if headers is None else headers)

    async def test_health_does_not_call_model(self):
        result = await self.client.get("/health")
        self.assertEqual(result.status, 200)
        self.assertEqual(self.provider.calls, 0)

    async def test_missing_auth_and_invalid_schema(self):
        response = await self.post(headers={})
        self.assertEqual(response.status, 401)
        response = await self.post({"contract_version": 99})
        self.assertEqual(response.status, 422)
        self.assertEqual(self.provider.calls, 0)

    async def test_completed_request_is_cached(self):
        first = await self.post()
        self.assertEqual(first.status, 200)
        first_data = await first.json()
        second = await self.post()
        self.assertEqual(await second.json(), first_data)
        self.assertEqual(self.provider.calls, 1)

    async def test_same_id_different_contents_is_conflict(self):
        await self.post()
        altered = request_data().model_dump()
        altered["brief"]["width_mm"] = 1500
        response = await self.post(altered)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.provider.calls, 1)

    async def test_parallel_duplicate_does_not_charge_twice(self):
        self.provider.release = asyncio.Event()
        first_task = asyncio.create_task(self.post())
        await self.provider.started.wait()
        try:
            response = await self.post()
            self.assertEqual(response.status, 409)
        finally:
            self.provider.release.set()
            first = await first_task
            await first.read()
        self.assertEqual(self.provider.calls, 1)

    async def test_failure_can_be_retried_explicitly(self):
        self.provider.failed = True
        response = await self.post()
        self.assertEqual(response.status, 502)
        self.provider.failed = False
        response = await self.post()
        self.assertEqual(response.status, 200)
        self.assertEqual(self.provider.calls, 2)

    async def test_bot_http_client_checks_contract(self):
        client = SketchClient(str(self.client.make_url("")).rstrip("/"), "secret")
        image, model = await client.generate(request_data())
        self.assertEqual(image, PNG)
        self.assertEqual(model, "test-model")

    async def test_cached_result_survives_service_restart(self):
        await self.post()
        await self.client.close()
        provider = FakeProvider()
        restarted = TestClient(TestServer(create_app(provider, self.temp.name, "secret")))
        await restarted.start_server()
        self.addAsyncCleanup(restarted.close)
        response = await restarted.post("/v1/sketches", json=request_data().model_dump(),
                                       headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(provider.calls, 0)


class ApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def endpoint(self, handler):
        app = web.Application()
        app.router.add_post("/api", handler)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return str(server.make_url("/api"))

    async def test_dialogue_api_sends_history_and_photo_and_parses_brief(self):
        async def handler(request):
            payload = await request.json()
            self.assertFalse(payload["store"])
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertIn("input_image", [x["type"] for x in payload["input"][0]["content"]])
            self.assertIn("Добавь полку", payload["input"][0]["content"][0]["text"])
            return web.json_response({"status": "completed", "output": [{
                "type": "message", "content": [{"type": "output_text",
                "text": completed("Тумба с полкой").model_dump_json()}]}]})
        endpoint = await self.endpoint(handler)
        result = await DialogueClient("key", "model", endpoint).analyse(completed().brief,
            [{"role": "user", "text": "Добавь полку"}], request_data().references)
        self.assertEqual(result.brief.description, "Тумба с полкой")

    async def test_refusal_and_incomplete_output_are_errors(self):
        for data in ({"status": "incomplete", "output": []},
                     {"status": "completed", "output": [{"content": [{"type": "refusal"}]}]}):
            async def handler(request, response=data):
                return web.json_response(response)
            endpoint = await self.endpoint(handler)
            with self.assertRaises(DialogueError):
                await DialogueClient("key", "model", endpoint).analyse(completed().brief, [], [])

    async def test_image_provider_sends_edit_request_and_reads_png(self):
        async def handler(request):
            payload = await request.json()
            self.assertEqual(payload["output_format"], "png")
            self.assertIn("image_url", payload["images"][0])
            self.assertIn("НЕ наноси размерные", payload["prompt"])
            self.assertNotIn("input_fidelity", payload)
            return web.json_response({"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        endpoint = await self.endpoint(handler)
        image, model = await ImageProvider("key", "gpt-image-2", "medium", "1536x1024",
                                          endpoint).generate(request_data())
        self.assertEqual(base64.b64decode(image.data_base64), PNG)

    async def test_provider_does_not_leak_error_body_or_api_key(self):
        async def handler(request):
            return web.json_response({"error": "sensitive-key-secret"}, status=401)
        endpoint = await self.endpoint(handler)
        for operation, error in (
            (DialogueClient("sensitive-key-secret", "model", endpoint).analyse(completed().brief, [], []),
             DialogueError),
            (ImageProvider("sensitive-key-secret", "model", "medium", "auto", endpoint).generate(request_data()),
             ProviderError)):
            with self.assertRaises(error) as caught:
                await operation
            self.assertNotIn("sensitive-key-secret", str(caught.exception))
