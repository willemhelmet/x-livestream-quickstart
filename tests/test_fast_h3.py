"""Offline FastH3 contracts; no provider or local-runtime calls are made."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from dataclasses import dataclass

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

try:
    from server import Studio
    from config import Config, FAST_MODEL, PresetError, load_preset
    from fast_narrative import FastNarrativeUpsampler, compile_fast_moment
    from reactor_link import ReactorLink
except (ImportError, ModuleNotFoundError) as exc:  # local-only requirements are optional
    Studio = Config = FAST_MODEL = PresetError = load_preset = None
    FastNarrativeUpsampler = compile_fast_moment = ReactorLink = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = ""


@unittest.skipIf(Studio is None, "runtime dependencies unavailable: " + _IMPORT_ERROR)
class FastServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = Studio(Path(self.tmp.name), {
            "PATH": os.environ.get("PATH", ""), "REACTOR_API_KEY": "r", "XAI_API_KEY": "x"})

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_fast_paid_gate_premise_and_image_validation_before_spawn(self):
        with patch("server.shutil.which", return_value="ffmpeg"), patch.object(self.studio, "status", return_value={"capabilities": {"h3": True}, "credentials": {"REACTOR_API_KEY": True, "XAI_API_KEY": True}}), patch("server.asyncio.create_subprocess_exec") as spawn:
            for kwargs in ({"confirm_paid": False, "premise": "x"}, {"confirm_paid": True, "premise": ""}, {"confirm_paid": True, "premise": "x", "use_starting_frame": True}):
                with self.assertRaises(ValueError):
                    await self.studio.start(source="fast_h3", **kwargs)
            spawn.assert_not_called()

    async def test_fast_spawn_failure_is_local_and_writes_preset(self):
        with patch("server.shutil.which", return_value="ffmpeg"), patch.object(self.studio, "status", return_value={"capabilities": {"h3": True}, "credentials": {"REACTOR_API_KEY": True, "XAI_API_KEY": True}}), patch("server.asyncio.create_subprocess_exec", side_effect=OSError("offline")):
            with self.assertRaises(ValueError):
                await self.studio.start(source="fast_h3", confirm_paid=True, premise="A red kite", use_starting_frame=False)
        self.assertEqual(self.studio.phase, "failed")
        preset = json.loads((self.studio.run_dir / "fast-show.json").read_text())
        self.assertEqual(preset["model"], FAST_MODEL)
        self.assertEqual(self.studio.child_env()["REACTOR_MODEL"], FAST_MODEL)
        self.assertEqual(self.studio.child_env()["SINK"], "preview")

    async def test_starting_frame_copied_without_mutating_saved_show(self):
        show = Path(self.tmp.name) / "show.json"
        ref = Path(self.tmp.name) / "references" / "character-1.png"
        ref.parent.mkdir(); ref.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
        original = {"version": 3, "images": [{"reference": "references/character-1.png"}], "model": "reactor/h3-reference-to-video-turbo-realtime"}
        show.write_text(json.dumps(original)); self.studio.show_path = show.resolve()
        with patch("server.shutil.which", return_value="ffmpeg"), patch.object(self.studio, "status", return_value={"capabilities": {"h3": True}, "credentials": {"REACTOR_API_KEY": True, "XAI_API_KEY": True}}), patch("server.asyncio.create_subprocess_exec", side_effect=OSError):
            with self.assertRaises(ValueError):
                await self.studio.start(source="fast_h3", confirm_paid=True, premise="A scene", use_starting_frame=True)
        preset = json.loads((self.studio.run_dir / "fast-show.json").read_text())
        self.assertEqual(preset["starting_frame"], "references/character-1.png")
        self.assertTrue((self.studio.run_dir / preset["starting_frame"]).is_file())
        self.assertEqual(json.loads(show.read_text()), original)


@unittest.skipIf(Config is None, "runtime dependencies unavailable: " + _IMPORT_ERROR)
class FastConfigTests(unittest.TestCase):
    def test_fast_preset_and_traversal_contract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "references").mkdir(); (root / "references/character-1.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            path = root / "fast.json"; path.write_text(json.dumps({"model": FAST_MODEL, "style": "clean", "narrative": {"premise": "scene"}, "starting_frame": "references/character-1.png"}))
            self.assertEqual(load_preset(str(path))["model"], FAST_MODEL)
            bad = dict(json.loads(path.read_text()), starting_frame="references/../secret.png")
            path.write_text(json.dumps(bad))
            with self.assertRaises(PresetError): load_preset(str(path))
            path.write_text(json.dumps({'model': FAST_MODEL, 'style': 'natural', 'narrative': {'premise': 'A forest'}}))
            with patch.dict(os.environ, {'REACTOR_API_KEY': 'fake', 'XAI_API_KEY': 'fake'}, clear=True):
                config = Config.load(['--preset', str(path)])
                self.assertEqual(config.model, FAST_MODEL)
                self.assertEqual(config.reference_paths, [])
                self.assertEqual(config.narrative, {'premise': 'A forest'})
                with self.assertRaisesRegex(SystemExit, 'does not match'):
                    Config.load(['--preset', str(path), '--model', 'other'])


@unittest.skipIf(ReactorLink is None, "runtime dependencies unavailable: " + _IMPORT_ERROR)
class ReactorLinkFastTests(unittest.IsolatedAsyncioTestCase):
    def cfg(self, model=FAST_MODEL, refs=None):
        return SimpleNamespace(model=model, reference_paths=refs or [], local=False, api_key=None, local_url="http://127.0.0.1")

    async def test_fast_enqueue_starting_frame_once_and_h3_references(self):
        link = ReactorLink(self.cfg()); link._ready.set(); link._reactor = SimpleNamespace(send_command=AsyncMock(return_value={"type": "clip_queued", "data": {"clip": {"clip_id": 'one'}}}), disconnect=AsyncMock())
        link.starting_frame = {"id": "frame"}
        await link.send_command("enqueue", {"prompt": "p", "seconds": 5, "metadata": '{"m":1}', "reference_images": ["bad"]})
        sent = link._reactor.send_command.call_args.args[1]; self.assertEqual(sent["starting_frame"], {"id": "frame"}); self.assertNotIn("reference_images", sent)
        await link.send_command("enqueue", {"prompt": "q", "seconds": 5, "metadata": {}})
        self.assertNotIn("starting_frame", link._reactor.send_command.call_args.args[1])
        h3 = ReactorLink(self.cfg("reactor/h3-reference-to-video-turbo-realtime")); h3._ready.set(); h3._reactor = SimpleNamespace(send_command=AsyncMock(return_value={"type": "ok", "data": {}})); h3.reference_images=[{"id": 2}]
        await h3.send_command("enqueue", {"prompt": "p"}); self.assertEqual(h3._reactor.send_command.call_args.args[1]["reference_images"], [{"id": 2}])
        await link._teardown(); self.assertIsNone(link._reactor); self.assertFalse(link._ready.is_set())
        self.assertIsNone(link.starting_frame)
        self.assertFalse(link._opening_frame_used)

    async def test_rejected_enqueue_preserves_opening_image(self):
        link = ReactorLink(self.cfg())
        link._ready.set()
        link.starting_frame = {'upload_id': 'fresh'}
        link._reactor = SimpleNamespace(send_command=AsyncMock(side_effect=[None, {'type': 'clip_queued', 'data': {'clip': {'clip_id': 'accepted'}}}]))
        self.assertIsNone(await link.send_command('enqueue', {'prompt': 'p', 'seconds': 6}))
        self.assertFalse(link._opening_frame_used)
        await link.send_command('enqueue', {'prompt': 'p', 'seconds': 6})
        self.assertTrue(link._opening_frame_used)
        self.assertEqual(link._reactor.send_command.call_args.args[1]['starting_frame']['upload_id'], 'fresh')

    async def test_fast_session_initialization_and_fresh_handles_after_reconnect(self):
        @dataclass
        class Uploaded:
            upload_id: str
            name: str = 'seed.png'
            mime_type: str = 'image/png'
            size: int = 100

        sessions = []
        class Session:
            def __init__(self, model, **kwargs):
                self.model = model
                self.frames = {}
                self.commands = []
                self.uploads = []
                self.status = 'ready'
                self.session_id = str(len(sessions))
                self.closed = False
                sessions.append(self)
            def on(self, *args): pass
            def on_status(self, handler): pass
            def track(self, name): return SimpleNamespace(on_frame=lambda fn: self.frames.update({name: fn}))
            async def connect(self): self.frames['main_video'](SimpleNamespace(shape=(768, 1344, 3)))
            async def disconnect(self): self.closed = True
            async def upload_file(self, path):
                self.uploads.append(path)
                return Uploaded('session-' + self.session_id)
            async def send_command(self, command, data):
                self.commands.append((command, data))
                if command == 'get_state':
                    return {'type': 'state_update', 'data': {'clip_seconds_min': 6, 'clip_seconds_max': 12,
                            'generation_capacity': 5, 'playout_capacity': 3}}
                return {'type': 'accepted', 'data': {}}

        for refs in ([], [Path('seed.png')]):
            link = ReactorLink(self.cfg(refs=refs))
            with patch('reactor_link.Reactor', Session):
                for _ in range(2):
                    task = asyncio.create_task(link._run_session())
                    try:
                        await asyncio.wait_for(link._ready.wait(), 2)
                        await asyncio.wait_for(link.wait_first_state(), 2)
                        session = sessions[-1]
                        self.assertEqual(session.model, FAST_MODEL)
                        self.assertEqual(session.uploads, refs)
                        self.assertEqual((link.min_seconds, link.max_seconds), (6, 12))
                        self.assertEqual(link.canvas, (1344, 768))
                        self.assertEqual(session.commands, [('get_state', {}), ('set_canvas', {'aspect': '16:9'}),
                                         ('set_flush_on_clip_end', {'enabled': False}), ('set_autoplay', {'enabled': True})])
                        self.assertEqual(link.reference_images, [])
                        if refs:
                            self.assertEqual(link.starting_frame['upload_id'], 'session-' + session.session_id)
                        else:
                            self.assertIsNone(link.starting_frame)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    self.assertTrue(session.closed)
                    self.assertIsNone(link.starting_frame)


@unittest.skipIf(compile_fast_moment is None, "runtime dependencies unavailable: " + _IMPORT_ERROR)
class FastNarrativeTests(unittest.IsolatedAsyncioTestCase):
    def test_compile_bounds_title_and_utf8(self):
        self.assertEqual(compile_fast_moment({"title": "x", "prompt": "p", "seconds": 99}, 5, 15).seconds, 15)
        for raw in ({"title": "", "prompt": "p", "seconds": 5}, {"title": "x", "prompt": "é" * 451, "seconds": 5}, {"title": "x", "prompt": "p", "seconds": True}):
            with self.assertRaises(ValueError): compile_fast_moment(raw, 5, 15)

    async def test_repair_and_scene_group(self):
        bad = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="bad"))])
        good = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"title": "ok", "prompt": "beat", "seconds": 7})))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=[bad, good]))))
        with patch("fast_narrative.grok_client", return_value=client):
            writer = FastNarrativeUpsampler(api_key="x", model="grok", style="s", narrative={"premise":"p"})
            group = await writer.upsample("idea", "a", "chat", 5, 10)
        self.assertEqual(group.title, "ok"); self.assertEqual(client.chat.completions.create.await_count, 2)
