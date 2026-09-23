"""Configuration for the reference-model livestream worker.

Everything comes from the environment (a `.env` file is loaded when present),
with a handful of CLI overrides for the things you flip per run. `Config.load`
is the only reader; the rest of the client takes a `Config` and never touches
`os.environ`.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from narrative import validate_narrative, reference_files, MODEL_NAME
from grok import GROK_MODEL, XAI_BASE_URL
FAST_MODEL = 'reactor/fast-h3'


def read_options(argv=None):
    parser = argparse.ArgumentParser(
        description="Reference-conditioned narrative livestream worker (see README.md)."
    )
    parser.add_argument("--env-file", default=None, help="path to a .env file")
    parser.add_argument("--model", default=None, help="override REACTOR_MODEL")
    parser.add_argument("--api-key", default=None, help="override REACTOR_API_KEY")
    parser.add_argument(
        "--local", action="store_true", help="drive a local `reactor run` (no key)"
    )
    parser.add_argument(
        "--local-url", default=None,
        help="local runtime URL (default REACTOR_LOCAL_URL or http://localhost:8080)",
    )
    parser.add_argument("--sink", default=None, choices=("preview", "noop"))
    parser.add_argument("--preset", default=None, help="override PRESET")
    args = parser.parse_args(argv)

    if args.env_file:
        load_dotenv(args.env_file, override=True)
    # The local dashboard supplies an explicit environment; never discover a
    # parent project's .env (which may contain a live destination).
    return args


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


class PresetError(ValueError):
    """A preset file is missing or malformed."""


def presets_dir() -> Path:
    """The `presets/` folder next to this module."""
    return Path(__file__).parent / "presets"


def available_presets() -> list[str]:
    """Preset names loadable right now, read fresh from the folder.

    Scanned on every call, so a JSON dropped into `presets/` mid-run is
    immediately switchable (see `admin.py`'s `!switch`).
    """
    return sorted(path.stem for path in presets_dir().glob("*.json"))


def load_preset(name_or_path: str) -> dict:
    """Load a show seed and resolve its character references relative to this file."""
    if "/" in name_or_path or name_or_path.endswith(".json"):
        path = Path(name_or_path)
    else:
        path = presets_dir() / f"{name_or_path}.json"
    if not path.is_file():
        raise PresetError(f"preset not found: {path}")
    try:
        preset = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PresetError(f"preset {path} is not valid JSON: {error}") from None
    if not isinstance(preset, dict):
        raise PresetError("Preset must be a JSON object")
    if preset.get('model') == FAST_MODEL:
        narrative = preset.get('narrative')
        premise = narrative.get('premise') if isinstance(narrative, dict) else None
        style = preset.get('style')
        if not isinstance(premise, str) or not premise.strip() or len(premise) > 2000:
            raise PresetError('FastH3 needs a scene of 1–2,000 characters')
        if not isinstance(style, str) or not style.strip() or len(style) > 2000:
            raise PresetError('FastH3 needs a valid visual style')
        paths = []
        if preset.get('starting_frame') is not None:
            import re
            reference = preset['starting_frame']
            if not isinstance(reference, str) or not re.fullmatch(r'references/character-1\.(png|jpg|webp)', reference):
                raise PresetError('Invalid FastH3 starting frame path')
            paths = reference_files({'characters': [{'name': 'Starting frame', 'reference': reference}]}, path.parent)
        return dict(model=FAST_MODEL, reference_paths=paths, narrative={'premise': premise.strip()},
                    style=style.strip(), idle_prompts=['Continue the scene naturally'])
    try:
        narrative = validate_narrative(preset.get("narrative"))
    except ValueError as error:
        raise PresetError(str(error)) from error
    style = preset.get("style")
    prompts = ["Continue the show naturally"]
    if not isinstance(style, str) or not style.strip() or len(style) > 2000:
        raise PresetError(f"preset {path} needs a non-empty string `style`")
    if not isinstance(prompts, list) or not all(isinstance(p, str) for p in prompts):
        raise PresetError(f"preset {path} needs `idle_prompts` as a list of strings")
    return {
        "model": MODEL_NAME,
        "reference_paths": reference_files(narrative, path.parent),
        "narrative": narrative,
        "style": style.strip(),
        "idle_prompts": [p.strip() for p in prompts if p.strip()],
    }


@dataclass(frozen=True)
class Config:
    """One immutable snapshot of everything the client is configured with."""

    reference_paths: list[Path]
    narrative: dict

    # Reactor
    model: str
    api_key: str | None
    local: bool
    local_url: str

    # Upsampling
    writer_api_key: str
    writer_base_url: str | None
    writer_model: str

    # Preset: the creative bundle (style + premade idle prompts)
    preset_name: str
    style: str
    idle_prompts: tuple[str, ...]

    # Grok prompt safety classifier (not connected to live chat yet).
    moderation_enabled: bool
    moderation_api_key: str
    moderation_base_url: str | None
    moderation_model: str

    # Idle filler
    idle_queue_target: int

    # Overlay (which overlay runs is code, main.py; this only switches it)
    overlay_enabled: bool

    # Sink
    sink: str  # "rtmp" | "noop"
    rtmp_url: str | None
    rtmp_video_bitrate_k: int
    rtmp_output_width: int
    rtmp_output_height: int
    rtmp_output_fps: int

    # Chat
    chat_command: str
    chat_cooldown_s: float
    twitch_channel: str | None
    youtube_video_id: str | None
    youtube_api_key: str | None

    @staticmethod
    def load(argv: list[str] | None = None) -> "Config":
        """Read `.env` + environment, apply CLI overrides, and validate."""
        args = read_options(argv)

        writer_api_key = os.environ.get("XAI_API_KEY", "")
        writer_base_url = XAI_BASE_URL

        preset_name = args.preset or os.environ.get("PRESET", "show.json")
        try:
            preset = load_preset(preset_name)
        except (PresetError, ValueError, OSError) as error:
            raise SystemExit(f"{error} (set PRESET or --preset)") from None

        selected_model = args.model or os.environ.get('REACTOR_MODEL') or preset['model']
        if selected_model != preset['model']:
            raise SystemExit('The selected model does not match the show configuration')
        config = Config(
            reference_paths=preset["reference_paths"],
            narrative=preset["narrative"],
            model=selected_model,
            api_key=args.api_key or os.environ.get("REACTOR_API_KEY") or None,
            local=args.local or _flag(os.environ.get("REACTOR_LOCAL")),
            local_url=args.local_url
            or os.environ.get("REACTOR_LOCAL_URL")
            or "http://localhost:8080",
            writer_api_key=writer_api_key,
            writer_base_url=writer_base_url,
            writer_model=GROK_MODEL,
            preset_name=preset_name,
            style=preset["style"],
            idle_prompts=tuple(preset["idle_prompts"]),
            moderation_enabled=_flag(os.environ.get("MODERATION_ENABLED", "1")),
            moderation_api_key=writer_api_key,
            moderation_base_url=XAI_BASE_URL,
            moderation_model=GROK_MODEL,
            idle_queue_target=int(os.environ.get("IDLE_QUEUE_TARGET", "1")),
            overlay_enabled=_flag(os.environ.get("OVERLAY_ENABLED", "1")),
            sink=(args.sink or os.environ.get("SINK", "noop")).lower(),
            rtmp_url=None,
            rtmp_video_bitrate_k=int(os.environ.get("RTMP_VIDEO_BITRATE_K", "4500")),
            rtmp_output_width=int(os.environ.get("RTMP_OUTPUT_WIDTH", "1280")),
            rtmp_output_height=int(os.environ.get("RTMP_OUTPUT_HEIGHT", "720")),
            rtmp_output_fps=int(os.environ.get("RTMP_OUTPUT_FPS", "30")),
            chat_command=os.environ.get("CHAT_COMMAND", "!prompt").strip(),
            chat_cooldown_s=float(os.environ.get("CHAT_COOLDOWN_S", "30")),
            twitch_channel=None,
            youtube_video_id=None,
            youtube_api_key=None,

        )
        config.validate()
        return config

    def validate(self) -> None:
        """Fail fast on contradictions instead of half-starting."""
        if self.model not in (MODEL_NAME, FAST_MODEL):
            raise SystemExit("Choose H3 Turbo Realtime or FastH3")
        if self.twitch_channel and not __import__("re").fullmatch(r"[a-zA-Z0-9_]{1,25}", self.twitch_channel):
            raise SystemExit("TWITCH_CHANNEL must be a channel login, without a URL or #")
        if not self.local and not self.api_key:
            raise SystemExit(
                "Either REACTOR_API_KEY (hosted) or --local / REACTOR_LOCAL=1 is required."
            )
        if not self.writer_api_key:
            raise SystemExit("XAI_API_KEY is required for Grok scene writing.")
        if self.rtmp_video_bitrate_k <= 0:
            raise SystemExit("RTMP_VIDEO_BITRATE_K must be positive.")
        if min(self.rtmp_output_width, self.rtmp_output_height, self.rtmp_output_fps) <= 0:
            raise SystemExit("RTMP output width, height and fps must be positive.")
        if self.rtmp_output_width % 2 or self.rtmp_output_height % 2:
            raise SystemExit("RTMP output width and height must be even for yuv420p.")
        if self.sink not in ("preview", "noop"):
            raise SystemExit(f"Unknown SINK {self.sink!r}; this starter only supports preview or noop.")
        if self.youtube_video_id and not self.youtube_api_key:
            raise SystemExit("YOUTUBE_VIDEO_ID needs YOUTUBE_API_KEY.")
        if not self.chat_command.startswith("!"):
            raise SystemExit("CHAT_COMMAND should start with '!' (e.g. !prompt).")
