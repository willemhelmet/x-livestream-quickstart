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
            for kwargs in ({"confirm_paid": False, "premise": "x"}, {"confirm_paid": True, "premise": ""}, {"confirm_paid": True, "premise": "x", "use_starting_frame": True},
                           {"confirm_paid": True, "premise": "x", "clip_seconds": 6},
                           {"confirm_paid": True, "premise": "x", "clip_seconds": True},
                           {"confirm_paid": True, "premise": "x", "continue_scenes": "true"}):
                with self.assertRaises(ValueError):
                    await self.studio.start(source="fast_h3", **kwargs)
            spawn.assert_not_called()

    async def test_fast_spawn_failure_is_local_and_writes_preset(self):
        with patch("server.shutil.which", return_value="ffmpeg"), patch.object(self.studio, "status", return_value={"capabilities": {"h3": True}, "credentials": {"REACTOR_API_KEY": True, "XAI_API_KEY": True}}), patch("server.asyncio.create_subprocess_exec", side_effect=OSError("offline")):
            with self.assertRaises(ValueError):
                await self.studio.start(source="fast_h3", confirm_paid=True, premise="A red kite", use_starting_frame=False,
                                        style='Hand-drawn animation with quiet strings', clip_seconds=10.125, continue_scenes=False)
        self.assertEqual(self.studio.phase, "failed")
        preset = json.loads((self.studio.run_dir / "fast-show.json").read_text())
        self.assertEqual(preset["model"], FAST_MODEL)
        self.assertEqual(preset['style'], 'Hand-drawn animation with quiet strings')
        self.assertEqual(preset['clip_seconds'], 10.125)
        self.assertFalse(preset['continue_scenes'])
        self.assertEqual(self.studio.child_env()["REACTOR_MODEL"], FAST_MODEL)
        self.assertEqual(self.studio.child_env()["SINK"], "preview")
        self.assertEqual(self.studio.child_env()['STUDIO_PARENT_PID'], str(os.getpid()))

    async def test_reference_image_copied_without_mutating_saved_show(self):
        show = Path(self.tmp.name) / "show.json"
        ref = Path(self.tmp.name) / "references" / "character-1.png"
        ref.parent.mkdir(); ref.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
        original = {"version": 3, "images": [{"reference": "references/character-1.png"}], "model": "reactor/h3-reference-to-video-turbo-realtime"}
        show.write_text(json.dumps(original)); self.studio.show_path = show.resolve()
        with patch("server.shutil.which", return_value="ffmpeg"), patch.object(self.studio, "status", return_value={"capabilities": {"h3": True}, "credentials": {"REACTOR_API_KEY": True, "XAI_API_KEY": True}}), patch("server.asyncio.create_subprocess_exec", side_effect=OSError):
            with self.assertRaises(ValueError):
                await self.studio.start(source="fast_h3", confirm_paid=True, premise="A scene", use_starting_frame=True)
        preset = json.loads((self.studio.run_dir / "fast-show.json").read_text())
        self.assertEqual(preset["reference_image"], "references/character-1.png")
        self.assertTrue((self.studio.run_dir / preset["reference_image"]).is_file())
        self.assertEqual(json.loads(show.read_text()), original)

    async def test_creative_files_copied_in_order_into_worker_config(self):
        payload = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        ids = [(await self.studio.upload_creative(payload,'image'))['uploadedAsset']['id'] for _ in range(2)]
        creative = dict(camera='Locked medium shot', images=[dict(id=id,kind='person',description='Character ' + str(i)) for i,id in enumerate(ids)], firstFrame=ids[1])
        with patch('server.shutil.which',return_value='ffmpeg'), patch.object(self.studio,'status',return_value={'capabilities':{'h3':True},'credentials':{'REACTOR_API_KEY':True,'XAI_API_KEY':True}}), patch('server.asyncio.create_subprocess_exec',side_effect=OSError):
            with self.assertRaises(ValueError):
                await self.studio.start(source='fast_h3',confirm_paid=True,premise='A scene',creative=creative)
        path = self.studio.run_dir / 'fast-show.json'
        config = load_preset(str(path))
        self.assertEqual(config['narrative']['creative'], creative)
        self.assertEqual([p.stem for p in config['creative_paths']['images']], ids)
        self.assertEqual(config['creative_paths']['firstFrame'][0].stem,ids[1])
        preset = json.loads(path.read_text())
        preset['creative_files']['images'][0] = '../credentials.json'
        path.write_text(json.dumps(preset))
        with self.assertRaises(PresetError): load_preset(str(path))


@unittest.skipIf(Config is None, "runtime dependencies unavailable: " + _IMPORT_ERROR)
class FastConfigTests(unittest.TestCase):
    def test_fast_preset_and_traversal_contract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "references").mkdir(); (root / "references/character-1.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            path = root / "fast.json"; path.write_text(json.dumps({"model": FAST_MODEL, "style": "clean", "narrative": {"premise": "scene"}, "reference_image": "references/character-1.png"}))
            self.assertEqual(load_preset(str(path))["model"], FAST_MODEL)
            bad = dict(json.loads(path.read_text()), reference_image="references/../secret.png")
            path.write_text(json.dumps(bad))
            with self.assertRaises(PresetError): load_preset(str(path))
            path.write_text(json.dumps(dict(json.loads(path.read_text()), model='reactor/h3-omni')))
            with self.assertRaisesRegex(PresetError, 'Unsupported Reactor model'):
                load_preset(str(path))
            path.write_text(json.dumps({'model': FAST_MODEL, 'style': 'natural', 'narrative': {'premise': 'A forest'}}))
            with patch.dict(os.environ, {'REACTOR_API_KEY': 'fake', 'XAI_API_KEY': 'fake'}, clear=True):
                config = Config.load(['--preset', str(path)])
                self.assertEqual(config.model, FAST_MODEL)
                self.assertEqual(config.reference_paths, [])
                self.assertEqual(config.narrative, {'premise': 'A forest'})
                self.assertEqual(config.clip_seconds, 8)
                self.assertTrue(config.continue_scenes)
                with self.assertRaisesRegex(SystemExit, 'does not match'):
                    Config.load(['--preset', str(path), '--model', 'other'])

    def test_published_model_contract_identifier(self):
        self.assertEqual(FAST_MODEL, 'reactor/fast-h3-omni')


@unittest.skipIf(ReactorLink is None, "runtime dependencies unavailable: " + _IMPORT_ERROR)
class ReactorLinkFastTests(unittest.IsolatedAsyncioTestCase):
    def cfg(self, model=FAST_MODEL, refs=None):
        return SimpleNamespace(model=model, reference_paths=refs or [], local=False, api_key=None, local_url="http://127.0.0.1")

    async def test_director_preroll_rebuffer_and_sequential_continuation(self):
        from director import Director
        from upsampler import Scene, SceneGroup
        config = self.cfg(); config.continue_scenes = True; config.playback_buffer_seconds = 16
        link = ReactorLink(config); link._ready.set()
        link.state.update(autoplay=False, playing=False, playout_capacity=3)
        async def command(name, data):
            if name == 'set_autoplay':
                return {'type':'autoplay_accepted','data':{}}
            return {'type':'clip_queued','data':{'clip':{'clip_id':'second','seconds':8,'prompt':data['prompt']}}}
        link._reactor = SimpleNamespace(send_command=AsyncMock(side_effect=command))
        writer = SimpleNamespace(record_queued=lambda clip: None)
        director = Director(link,writer,SimpleNamespace(),cooldown_s=0)
        link.playout_clips = [dict(clip_id='first',ready=True,seconds=8)]
        await director._maintain_playback_buffer()
        link._reactor.send_command.assert_not_awaited()
        link.playout_clips.append(dict(clip_id='reserve',ready=True,seconds=8))
        await director._maintain_playback_buffer()
        self.assertEqual(link._reactor.send_command.call_args.args, ('set_autoplay',{'enabled':True}))
        link.playout_clips = []; link.state['playing'] = False
        await director._maintain_playback_buffer()
        self.assertEqual(link.buffer.phase,'rebuffering')
        self.assertFalse(link.state['autoplay'])
        link.state['generation_queued'] = 1
        group = SceneGroup('group','Next','auto','idle','',[Scene('A bowl turns.',8)],True)
        task = asyncio.create_task(director._enqueue_group(group))
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        self.assertFalse(any(call.args[0] == 'enqueue' for call in link._reactor.send_command.call_args_list))
        link._on_message({'type':'clip_generated','data':{'clip':{'clip_id':'finished-first','seconds':8,'build_seconds':12}}})
        link.state['generation_queued'] = 0
        await asyncio.wait_for(task,1)
        queued = link._reactor.send_command.call_args.args[1]
        self.assertEqual(queued['continue_from_clip_id'],'finished-first')
        self.assertEqual(link.build_metrics,dict(buildSeconds=12.0,generationRate=.67))

    async def test_continuation_uses_only_generated_clips_and_resets_with_session(self):
        config = self.cfg()
        config.continue_scenes = True
        config.clip_seconds = 8
        link = ReactorLink(config)
        link._ready.set()
        link._reactor = SimpleNamespace(send_command=AsyncMock(return_value={'type': 'clip_queued', 'data': {'clip': {'clip_id': 'next'}}}))
        self.assertEqual((link.min_seconds, link.max_seconds), (8, 8))
        link._on_message({'type': 'clip_queued', 'data': {'clip': {'clip_id': 'not-generated'}}})
        await link.send_command('enqueue', {'prompt': 'Opening', 'seconds': 8})
        self.assertNotIn('continue_from_clip_id', link._reactor.send_command.call_args.args[1])
        link._on_message({'type': 'clip_generated', 'data': {'clip': {'clip_id': 'generated-source'}}})
        await link.send_command('enqueue', {'prompt': 'Continue', 'seconds': 8})
        sent = link._reactor.send_command.call_args.args[1]
        self.assertEqual(sent['continue_from_clip_id'], 'generated-source')
        self.assertEqual(sent['continue_mode'], 'latent')
        link._on_message({'type': 'session_reset', 'data': {}})
        await link.send_command('enqueue', {'prompt': 'New opening', 'seconds': 8})
        self.assertNotIn('continue_from_clip_id', link._reactor.send_command.call_args.args[1])

    async def test_fast_enqueue_reference_image_once_and_h3_references(self):
        link = ReactorLink(self.cfg()); link._ready.set(); link._reactor = SimpleNamespace(send_command=AsyncMock(return_value={"type": "clip_queued", "data": {"clip": {"clip_id": 'one'}}}), disconnect=AsyncMock())
        link.starting_frame = {"id": "frame"}
        await link.send_command("enqueue", {"prompt": "p", "seconds": 5, "metadata": '{"m":1}', "reference_images": ["bad"]})
        sent = link._reactor.send_command.call_args.args[1]; self.assertEqual(sent["reference_image"], {"id": "frame"}); self.assertNotIn("reference_images", sent)
        self.assertEqual(sent['seconds'], 5.875)
        await link.send_command("enqueue", {"prompt": "q", "seconds": 5, "metadata": {}})
        self.assertNotIn("reference_image", link._reactor.send_command.call_args.args[1])
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
        self.assertEqual(link._reactor.send_command.call_args.args[1]['reference_image']['upload_id'], 'fresh')

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
                    return {'type': 'state_update', 'data': {'clip_seconds_choices': [5.875, 8, 14.375],
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
                        self.assertEqual((link.min_seconds, link.max_seconds), (5.875, 14.375))
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

        config = self.cfg(); config.playback_buffer_seconds = 16
        config.creative_paths = {'images':[Path('image-1.png'),Path('image-2.png')], 'audios':[Path('voice.wav')]}
        link = ReactorLink(config)
        with patch('reactor_link.Reactor', Session):
            handles = []
            for _ in range(2):
                task = asyncio.create_task(link._run_session())
                try:
                    await asyncio.wait_for(link._ready.wait(),2)
                    self.assertEqual(sessions[-1].uploads, [Path('image-1.png'),Path('image-2.png'),Path('voice.wav')])
                    self.assertEqual(sessions[-1].commands[-1],('set_autoplay',{'enabled':False}))
                    handles.append(link.creative_refs['images'][0]['upload_id'])
                finally:
                    task.cancel(); await asyncio.gather(task,return_exceptions=True)
                self.assertEqual(link.creative_refs,{})
            self.assertNotEqual(handles[0],handles[1])


@unittest.skipIf(compile_fast_moment is None, "runtime dependencies unavailable: " + _IMPORT_ERROR)
class FastNarrativeTests(unittest.IsolatedAsyncioTestCase):
    def test_compile_bounds_title_and_utf8(self):
        self.assertEqual(compile_fast_moment({"title": "x", "prompt": "p", "seconds": 99}, 5, 15).seconds, 15)
        for raw in ({"title": "", "prompt": "p", "seconds": 5}, {"title": "x", "prompt": "é" * 451, "seconds": 5}, {"title": "x", "prompt": "p", "seconds": True}):
            with self.assertRaises(ValueError): compile_fast_moment(raw, 5, 15)

    async def test_repair_and_scene_group(self):
        bad = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="bad"))])
        good = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"title": "ok", "action": "A chef sets down a bowl.", "camera":"The camera holds a steady medium shot.","sound":"Quiet kitchen ambience.","music":"N/A","speech":None,"seconds":7})))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=[bad, good]))))
        with patch("fast_narrative.grok_client", return_value=client):
            writer = FastNarrativeUpsampler(api_key="x", model="grok", style="s", narrative={"premise":"p"})
            group = await writer.upsample("idea", "a", "chat", 5, 10)
        self.assertEqual(group.title, "ok"); self.assertEqual(client.chat.completions.create.await_count, 2)

    async def test_creative_prompt_preserves_roles_and_explicit_direction(self):
        from creative import assemble_prompt, validate_creative
        creative = validate_creative(dict(camera='Locked medium shot', sound='Rain on glass', music='No music',
            dialogue='Aki says "Hello" in a low voice', images=[dict(id='a'*24, kind='person', description='Aki the chef')],
            audios=[dict(id='b'*24, kind='voice', description='Voice of Aki in <Picture 1>')]))
        speech = dict(speaker='Aki', voice='a low voice', language='English', text='Hello.', picture=1, audio=1)
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'title':'Welcome','action':'Aki serves soup.','camera':'A slow push in.','sound':'Rain.','music':'N/A','speech':speech,'seconds':8})))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response))))
        with patch('fast_narrative.grok_client', return_value=client):
            writer = FastNarrativeUpsampler(api_key='fake', model='grok', style='Warm light', narrative={'premise':'A ramen shop','creative':creative})
            group = await writer.upsample('', 'operator', 'idle', 8, 8, generated=True)
        prompt = group.scenes[0].prompt
        self.assertEqual(prompt, assemble_prompt('Aki serves soup.', 'Warm light', creative, speech=speech))
        self.assertIn('<Subject 1> is Aki the chef in <Picture 1>', prompt)
        self.assertIn('<Audio 1> is the voice-timbre reference', prompt)
        for text in ('Locked medium shot', 'overall_soundscape: Rain on glass', 'non_diegetic_music: No music', '<d>[English] Hello.</d>'):
            self.assertIn(text, prompt)
        context = json.loads(client.chat.completions.create.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(context['show']['creative']['images'][0]['label'], '<Picture 1>')
        self.assertNotIn('id', context['show']['creative']['images'][0])

    def test_reference_constraints_and_soundtrack_precedence(self):
        from creative import assemble_prompt, validate_creative
        image = dict(id='a'*24, kind='scene', description='A pottery workshop')
        audio = dict(id='b'*24, kind='soundtrack', description='Soft rain and piano')
        for invalid in ({'audios':[audio]}, {'firstFrame':'a'*24}, {'images':[image]*10},
                        {'images':[dict(image, id='../private')]}, {'images':[dict(image, description='')]},
                        {'images':[image], 'audios':[dict(audio,id=f'{i:024x}') for i in range(3)]}):
            with self.assertRaises(ValueError): validate_creative(invalid)
        valid = validate_creative({'images':[image], 'audios':[audio], 'sound':'Ignored rain','music':'Ignored brass','dialogue':'Ignored voice'})
        prompt = assemble_prompt('A cup spins.', 'Warm', valid)
        self.assertIn('complete audio track, reused unchanged', prompt)
        for label in ('Sound:', 'Music:', 'Dialogue:'):
            self.assertNotIn(label, prompt)
        validate_creative({'images':[image], 'audios':[dict(audio,kind='voice',id=f'{i:024x}') for i in range(3)]}, continuation=False)

    async def test_ordered_refs_and_opening_keyframes_on_wire(self):
        cfg = SimpleNamespace(model=FAST_MODEL, reference_paths=[], continue_scenes=True)
        link = ReactorLink(cfg); link._ready.set()
        link._reactor = SimpleNamespace(send_command=AsyncMock(return_value={'type':'clip_queued','data':{'clip':{'clip_id':'one'}}}))
        link.creative_refs = {'images':[{'upload_id':'i1'},{'upload_id':'i2'}], 'audios':[{'upload_id':'a1'}],
                              'firstFrame':[{'upload_id':'first'}], 'lastFrame':[{'upload_id':'last'}]}
        await link.send_command('enqueue', {'prompt':'Opening','seconds':8})
        first = link._reactor.send_command.call_args.args[1]
        self.assertEqual(first['reference_images'], [{'upload_id':'i1'},{'upload_id':'i2'}])
        self.assertEqual(first['reference_audios'], [{'upload_id':'a1'}])
        self.assertEqual(first['first_frame_image'], {'upload_id':'first'})
        self.assertEqual(first['last_frame_image'], {'upload_id':'last'})
        link._on_message({'type':'clip_generated','data':{'clip':{'clip_id':'one'}}})
        await link.send_command('enqueue', {'prompt':'Continue','seconds':8})
        next_clip = link._reactor.send_command.call_args.args[1]
        self.assertEqual(next_clip['continue_mode'], 'latent')
        self.assertEqual(next_clip['reference_images'], first['reference_images'])
        self.assertEqual(next_clip['reference_audios'], first['reference_audios'])
        self.assertNotIn('first_frame_image', next_clip); self.assertNotIn('last_frame_image', next_clip)
