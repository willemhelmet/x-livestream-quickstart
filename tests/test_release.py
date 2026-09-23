import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_check', Path(__file__).resolve().parents[1] / 'scripts/check_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseChecks(unittest.TestCase):
    def test_allowlist_excludes_private_generated_and_review_files(self):
        for path in ('.env', '.local/credentials.json', '.local/runs/clip.mp4', 'web-review/screenshot.png',
                     'runtime/__pycache__/module.pyc', '.git/config', '../server.py', 'web/key.pem'):
            self.assertFalse(release.allowed(path), path)
        for path in ('server.py', '.env.example', 'web/app.js', 'runtime/chat/__init__.py'):
            self.assertTrue(release.allowed(path), path)

    def test_saved_key_leak_detected_without_printing_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / '.local').mkdir()
            secret = 'example-private-value-for-check'
            (root / '.local' / 'credentials.json').write_text(json.dumps({'REACTOR_API_KEY': secret}))
            (root / 'server.py').write_text('value = ' + repr(secret))
            names, findings = release.inspect(root)
            self.assertEqual(names, ['server.py'])
            self.assertTrue(findings)
            self.assertNotIn(secret, str(findings))

    def test_example_values_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / '.env.example').write_text('REACTOR_API_KEY=not-empty\n')
            (root / 'server.py').symlink_to(root / '.env.example')
            _, findings = release.inspect(root)
            self.assertTrue(any('example credential' in value for value in findings))
            self.assertTrue(any('symlink' in value for value in findings))

    def test_current_release_candidates_pass(self):
        _, findings = release.inspect(Path(__file__).resolve().parents[1])
        self.assertEqual(findings, [])
