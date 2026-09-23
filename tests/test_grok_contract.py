"""Offline, wire-level contracts for the xAI/OpenAI-compatible SDK boundary."""

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import httpx2
    from openai import AsyncOpenAI
except ImportError:  # requirements-local intentionally permits a local-only install
    httpx2 = AsyncOpenAI = None


RUNTIME = Path(__file__).parents[1] / "runtime"
if AsyncOpenAI is not None:
    sys.path.insert(0, str(RUNTIME))
    from autoseed import prepare_show
    from config import Config
    from grok import GROK_MODEL, XAI_BASE_URL, grok_client
    from moderator import Moderator
    from narrative import NarrativeUpsampler, MODEL_NAME


@unittest.skipUnless(AsyncOpenAI is not None, "openai/httpx2 requirements-local are not installed")
class GrokSDKContractTests(unittest.IsolatedAsyncioTestCase):
    def client_with(self, bodies):
        requests = []

        async def handler(request):
            requests.append(request)
            content = bodies.pop(0)
            if isinstance(content, int):
                return httpx2.Response(content, request=request)
            return httpx2.Response(200, json={
                "choices": [{"message": {"content": content}}],
            }, request=request)

        client = AsyncOpenAI(
            api_key="fake-xai-key", base_url=XAI_BASE_URL,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
        return client, requests

    def assert_xai_request(self, request):
        self.assertEqual(request.url.host, "api.x.ai")
        self.assertEqual(request.url.path, "/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer fake-xai-key")
        body = json.loads(request.content)
        self.assertEqual(body["model"], GROK_MODEL)
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        return body

    async def test_production_factory_is_xai_only(self):
        client = grok_client("fake-xai-key")
        try:
            self.assertEqual(str(client.base_url), XAI_BASE_URL + "/")
            self.assertEqual(GROK_MODEL, "grok-4.7")
            with self.assertRaises(ValueError):
                grok_client("fake-xai-key", base_url="https://api.openai.com/v1")
            for invalid_key in ("", "   ", None, 7):
                with self.assertRaises(ValueError):
                    grok_client(invalid_key)
        finally:
            await client.close()

    async def test_autoseed_serializes_image_and_preserves_reference(self):
        ready = {
            "name": "Cafe", "style": "warm",
            "narrative": {"premise": "A cafe", "setting": "Cafe", "ambience": "Cozy",
                          "characters": [{"name": "Pip", "description": "Small bird", "voice": "Bright"}]},
        }
        client, requests = self.client_with([json.dumps(ready)])
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "references").mkdir()
                # A real minimal PNG: vision_image must read and re-encode it.
                (root / "references" / "character-1.png").write_bytes(
                    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"))
                seed = {"version": 3, "model": MODEL_NAME, "mode": "auto", "overrides": {"premise": "A cafe"},
                        "images": [{"reference": "references/character-1.png"}]}
                path = root / "show.json"
                path.write_text(json.dumps(seed))
                result = await prepare_show(path, env={"XAI_API_KEY": "fake-xai-key"}, client=client)
                self.assertEqual(result["narrative"]["characters"][0]["reference"], "references/character-1.png")
            self.assertEqual(len(requests), 1)
            body = self.assert_xai_request(requests[0])
            image = next(item for item in body["messages"][1]["content"] if item["type"] == "image_url")
            self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))
            base64.b64decode(image["image_url"]["url"].split(",", 1)[1])
        finally:
            await client.close()

    async def test_narrative_repairs_invalid_json_over_sdk(self):
        scene = {"title": "Pour", "action": "Pip pours tea", "framing": "Medium shot", "characters": [1],
                 "dialogue": "", "speaker": None, "seconds": 4}
        client, requests = self.client_with(["not-json", json.dumps(scene)])
        narrative = {"premise": "Cafe", "setting": "Cafe", "ambience": "Warm",
                     "characters": [{"name": "Pip", "description": "Bird", "voice": "Bright",
                                     "reference": "references/character-1.png"}]}
        try:
            with patch("narrative.grok_client", return_value=client):
                writer = NarrativeUpsampler(api_key="fake-xai-key", model=GROK_MODEL, style="cinematic", narrative=narrative)
                result = await writer.next_scene({"min_seconds": 3, "max_seconds": 8})
            self.assertEqual(result, scene)
            self.assertEqual(len(requests), 2)
            first = self.assert_xai_request(requests[0])
            repaired = self.assert_xai_request(requests[1])
            self.assertEqual(repaired["messages"][-1]["role"], "user")
            self.assertIn("Repair the complete JSON", repaired["messages"][-1]["content"])
            self.assertEqual(first["messages"][1]["role"], "user")
        finally:
            await client.close()

    async def test_moderator_allow_reject_malformed_and_error_fail_closed(self):
        client, requests = self.client_with([
            json.dumps({"allowed": True, "reason": "fine"}),
            json.dumps({"allowed": False, "reason": "unsafe"}),
            json.dumps({"allowed": "yes", "reason": "bad type"}), 400,
        ])
        try:
            with patch("moderator.grok_client", return_value=client):
                moderator = Moderator("fake-xai-key", GROK_MODEL, True)
                self.assertIsNone(await moderator.review("a harmless idea"))
                self.assertEqual(await moderator.review("unsafe idea"), "Rejected by Grok safety review")
                self.assertEqual(await moderator.review("malformed"), "moderation unavailable")
                # A provider error must also fail closed.
                self.assertEqual(await moderator.review("provider error"), "moderation unavailable")
            self.assertEqual(len(requests), 4)
            for request in requests:
                self.assert_xai_request(request)
        finally:
            await client.close()

    async def test_config_uses_xai_settings_and_ignores_legacy_openai_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "references").mkdir()
            (root / "references" / "character-1.png").write_bytes(
                base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"))
            preset = {"style": "cinematic", "narrative": {"premise": "Cafe", "setting": "Cafe", "ambience": "Warm",
                "characters": [{"name": "Pip", "description": "Bird", "voice": "Bright", "reference": "references/character-1.png"}]}}
            preset_path = root / "show.json"
            preset_path.write_text(json.dumps(preset))
            environment = {"XAI_API_KEY": "fake-xai-key", "REACTOR_API_KEY": "fake-reactor-key",
                           "OPENAI_API_KEY": "legacy-key", "OPENAI_BASE_URL": "https://api.openai.com/v1",
                           "WRITER_MODEL": "legacy-writer", "WRITER_BASE_URL": "https://legacy.invalid/v1",
                           "MODERATION_MODEL": "legacy-moderator", "MODERATION_BASE_URL": "https://legacy.invalid/v1"}
            with patch.dict(os.environ, environment, clear=True):
                config = Config.load(["--preset", str(preset_path)])
            self.assertEqual(config.writer_api_key, "fake-xai-key")
            self.assertEqual(config.moderation_api_key, "fake-xai-key")
            self.assertEqual(config.writer_base_url, XAI_BASE_URL)
            self.assertEqual(config.moderation_base_url, XAI_BASE_URL)
            self.assertEqual(config.writer_model, GROK_MODEL)
            self.assertEqual(config.moderation_model, GROK_MODEL)


if __name__ == "__main__":
    unittest.main()
