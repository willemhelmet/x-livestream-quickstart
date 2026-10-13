"""Local relay contract: suggestions are operator-entered, not X chat ingestion."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from chat.local import LocalChatSource


class LocalChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_only_new_operator_suggestions(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder) / 'chat-inbox.jsonl'
            inbox.write_text(json.dumps({'source': 'operator', 'author': 'old', 'text': 'old idea'}) + '\n')
            received = []
            task = asyncio.create_task(LocalChatSource(str(inbox)).run(received.append))
            await asyncio.sleep(0.05)
            try:
                with inbox.open('a') as stream:
                    stream.write(json.dumps({'source': 'operator', 'author': '@viewer',
                                             'text': 'Make the sky green'}) + '\n')
                    stream.write(json.dumps({'source': 'x-live-chat', 'author': 'unknown',
                                             'text': 'This must not be ingested'}) + '\n')
                async def wait_for_message():
                    while not received:
                        await asyncio.sleep(0.02)
                await asyncio.wait_for(wait_for_message(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].source, 'operator')
            self.assertEqual(received[0].text, 'Make the sky green')


if __name__ == '__main__':
    unittest.main()
