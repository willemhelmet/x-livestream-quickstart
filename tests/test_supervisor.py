"""A real dashboard-parent crash must stop its generation loop."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from supervisor import parent_id


class SupervisorTests(unittest.TestCase):
    def test_invalid_owner_refuses_start(self):
        for value in ('1', '0', '-2', 'not-a-pid'):
            with patch.dict(os.environ, {'STUDIO_PARENT_PID': value}):
                with self.assertRaises(SystemExit):
                    parent_id()

    def test_worker_stops_after_real_parent_exits_without_cleanup(self):
        child_code = '''
import asyncio, pathlib, sys
sys.path.insert(0, sys.argv[1])
from supervisor import watch_parent
async def run():
    directory = pathlib.Path(sys.argv[3])
    stopped = asyncio.Event()
    watcher = asyncio.create_task(watch_parent(int(sys.argv[2]), stopped))
    (directory / 'ready').write_text('ready')
    calls = 0
    async with asyncio.timeout(5):
        while not stopped.is_set():
            calls += 1
            await asyncio.sleep(.05)
    await watcher
    (directory / 'stopped').write_text(str(calls))
asyncio.run(run())
'''
        parent_code = '''
import os, pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2],
                          str(os.getpid()), sys.argv[3]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
deadline = time.monotonic() + 4
while not (pathlib.Path(sys.argv[3]) / 'ready').exists() and time.monotonic() < deadline:
    time.sleep(.02)
os._exit(9)
'''
        with tempfile.TemporaryDirectory() as folder:
            parent = subprocess.run([sys.executable, '-c', parent_code, child_code,
                                     str(ROOT / 'runtime'), folder], capture_output=True, text=True, timeout=6)
            child_pid = int(parent.stdout.strip())
            try:
                self.assertEqual(parent.returncode, 9)
                done = Path(folder) / 'stopped'
                deadline = time.monotonic() + 5
                while not done.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(done.exists(), 'The worker survived loss of its dashboard parent')
                self.assertLess(int(done.read_text()), 30)
            finally:
                try:
                    os.kill(child_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    unittest.main()
