"""Operator-relayed suggestions from the loopback studio.

The file is scoped to one local run and starts reading at its current end, so
worker restarts cannot replay earlier suggestions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .base import ChatPrompt, ChatSource

logger = logging.getLogger(__name__)


class LocalChatSource(ChatSource):
    name = "operator"

    def __init__(self, inbox: str):
        self.path = Path(inbox)

    async def run(self, on_prompt):
        if not self.path.is_file() or self.path.is_symlink():
            raise RuntimeError("Local suggestion inbox is missing or is a symlink")
        with self.path.open("r", encoding="utf-8") as stream:
            stream.seek(0, 2)
            seen_ids = set()
            while True:
                line = stream.readline()
                if not line:
                    await asyncio.sleep(0.25)
                    continue
                if not line.endswith("\n"):
                    stream.seek(stream.tell() - len(line))
                    await asyncio.sleep(0.25)
                    continue
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        continue
                    text, author = value.get("text"), value.get("author")
                    message_id = value.get("id")
                    if message_id and message_id in seen_ids:
                        continue
                    if value.get("source") != "operator":
                        continue
                    if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 500
                            or not isinstance(author, str) or not 1 <= len(author.strip()) <= 50):
                        continue
                    on_prompt(ChatPrompt(source="operator", author=author.strip(),
                                         text=text.strip(), command=""))
                    if message_id:
                        seen_ids.add(message_id)
                except (ValueError, TypeError):
                    logger.warning("Ignoring malformed local scene suggestion")
