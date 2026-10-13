import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "runtime"))

from creative import assemble_prompt, validate_speech
from fast_narrative import FastNarrativeUpsampler, compile_omni_moment
from upsampler import Scene


def raw(speech=None, **overrides):
    value = dict(title="Beat", action="A red kite lifts over the field.", camera="Wide shot.",
                 sound="Wind and grass.", music="N/A", speech=speech, seconds=8)
    value.update(overrides)
    return value


class DialoguePromptTests(unittest.TestCase):
    def test_speech_validation_modes_and_limits(self):
        base = dict(speaker="Mira", voice="Warm alto", language="English", text="Hello there.", picture=None, audio=None)
        self.assertEqual(validate_speech(None, 8, {}), None)
        self.assertEqual(validate_speech(base, 8, {"dialogueMode": "exact", "exactDialogue": "Hello there."}), base)
        for creative in ({"dialogueMode": "off"}, {"audios": [{"id": "a", "kind": "soundtrack", "description": "full"}]}):
            with self.assertRaises(ValueError): validate_speech(base, 8, creative)
        for bad in (dict(base, text="too many words " * 5 + "."), dict(base, text="Hello there"),
                    dict(base, text="<d>Hello.</d>"), dict(base, text="Hello\nthere."),
                    dict(base, picture=0), dict(base, picture=True), dict(base, audio=1)):
            with self.assertRaises(ValueError): validate_speech(bad, 5.875, {"images": [], "audios": []})
        self.assertIsNotNone(validate_speech(dict(base, text="One two three four five six seven eight."), 5.875, {}))
        self.assertIsNotNone(validate_speech(dict(base, text="One two three four five six seven eight nine ten eleven twelve."), 8, {}))
        with self.assertRaises(ValueError): validate_speech(dict(base, text="One two three four five six seven eight nine."), 5.875, {})

    def test_exact_configured_fields_are_byte_exact(self):
        creative = {"dialogueMode": "exact", "exactDialogue": "Hello, world!", "dialogueSpeaker": "Mira", "dialogueVoice": "Warm alto", "dialogueLanguage": "English"}
        speech = dict(speaker="Mira", voice="Warm alto", language="English", text="Hello, world!", picture=None, audio=None)
        self.assertEqual(validate_speech(speech, 8, creative), speech)
        with self.assertRaises(ValueError): validate_speech(dict(speech, text="Hello world!"), 8, creative)
        with self.assertRaises(ValueError): validate_speech(None, 8, creative)

    def test_compile_shape_rejects_hidden_speech_and_returns_scene(self):
        scene = compile_omni_moment(raw(), 5.875, 8, "cinematic", {})
        self.assertIsInstance(scene, Scene)
        self.assertIn("integrated_multimodal_description: [Shot 1]", scene.prompt)
        for action in ("Mira says hello.", "Mira whispers.", "<d>Hello.</d>", "[Shot 1] A scene"):
            with self.assertRaises(ValueError): compile_omni_moment(raw(action=action), 5.875, 8, "s", {})
        with self.assertRaises(ValueError): compile_omni_moment(dict(raw(), prompt="x"), 5.875, 8, "s", {})

    def test_canonical_speech_and_references(self):
        creative = {"images": [{"id": "a" * 24, "kind": "person", "description": "a red-haired subject"}],
                    "audios": [{"id": "b" * 24, "kind": "voice", "description": "warm alto"}]}
        speech = dict(speaker="Mira", voice="Warm alto", language="English", text="Hello there.", picture=1, audio=1)
        prompt = compile_omni_moment(raw(speech=speech), 8, 8, "style", creative).prompt
        self.assertIn("<Subject 1> is a red-haired subject in <Picture 1>", prompt)
        self.assertIn("<Audio 1>", prompt)
        self.assertIn("(S1), says: <d>[English] Hello there.</d>", prompt)
        self.assertNotIn("(S1)<d>", prompt)
        quiet = compile_omni_moment(raw(), 8, 8, "style", creative).prompt
        self.assertNotIn("(S1)", quiet); self.assertNotIn("<d>", quiet); self.assertIn("lips closed", quiet)

    def test_prompt_sections_and_soundtrack(self):
        creative = {"images": [{"id": "a" * 24, "kind": "person", "description": "subject"}],
                    "audios": [{"id": "b" * 24, "kind": "soundtrack", "description": "track"}]}
        prompt = assemble_prompt("beat", "style", creative, camera="camera", sound="sound", music="music")
        for field in ("subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"):
            self.assertIn(field, prompt)
        self.assertNotIn("Camera:", prompt); self.assertNotIn("Dialogue:", prompt)
        with self.assertRaises(ValueError): compile_omni_moment(raw(speech={"x": 1}), 8, 8, "s", creative)

    def test_mocked_repair_and_committed_context(self):
        valid = raw(); invalid = dict(valid, action="Mira says hello.")
        responses = [invalid, valid]
        class Completions:
            async def create(self, **kwargs):
                payload = responses.pop(0)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
        fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        narrative = {"premise": "A kite", "creative": {}}
        with patch("fast_narrative.grok_client", return_value=fake):
            writer = FastNarrativeUpsampler(api_key="x", model="m", style="s", narrative=narrative)
        writer.record_queued({"prompt": "queued beat"})
        writer.record_played("session_ready", {})
        writer.record_queued({"prompt": "committed beat"})
        result = asyncio.run(writer.next_scene({"min_seconds": 8, "max_seconds": 8, "show": {}, "recent_committed_prompts": ["committed beat"]}))
        self.assertEqual(result["action"], valid["action"])
        self.assertEqual(len(responses), 0)


if __name__ == "__main__": unittest.main()
