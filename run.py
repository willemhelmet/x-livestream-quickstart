#!/usr/bin/env python3
"""After installing dependencies, run: python run.py"""
import shutil
import sys

if sys.version_info < (3, 12):
    raise SystemExit('Use Python 3.12 or newer for this starter.')
try:
    import aiohttp
    import dotenv
    import PIL
except ImportError:
    raise SystemExit('Install local dependencies first: python -m pip install -r requirements-local.txt')
if not shutil.which('ffmpeg'):
    raise SystemExit('FFmpeg is missing. On macOS: brew install ffmpeg. On Ubuntu: sudo apt install ffmpeg.')
from server import main
main()
