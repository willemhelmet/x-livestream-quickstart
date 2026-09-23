"""Transport-facing scene values. Prompt production lives in narrative.py."""
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Scene:
    prompt: str
    seconds: float

@dataclass(frozen=True)
class SceneGroup:
    group_id: str
    title: str
    author: str
    source: str
    raw_prompt: str
    scenes: list[Scene]
    generated: bool = False

class PromptUpsampler(Protocol):
    async def upsample(self, raw_prompt, author, source, min_seconds, max_seconds,
                       generated=False, max_chunks=None) -> SceneGroup: ...
