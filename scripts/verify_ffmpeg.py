"""Quick command-line check of FFmpeg/FFprobe detection.

Usage: python scripts/verify_ffmpeg.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tapesift.core.config import AppSettings          # noqa: E402
from tapesift.services import ffmpeg_service          # noqa: E402


def main() -> int:
    settings = AppSettings.load()
    ffmpeg = ffmpeg_service.find_executable("ffmpeg", settings.ffmpeg_path)
    ffprobe = ffmpeg_service.find_executable("ffprobe", settings.ffprobe_path)
    print(f"ffmpeg:  {ffmpeg or 'NOT FOUND'}")
    if ffmpeg:
        print(f"         {ffmpeg_service.get_version(ffmpeg)}")
        encoders = ffmpeg_service.detect_hw_encoders(ffmpeg)
        print(f"         hardware encoders: {', '.join(encoders) or 'none detected'}")
    print(f"ffprobe: {ffprobe or 'NOT FOUND'}")
    if ffprobe:
        print(f"         {ffmpeg_service.get_version(ffprobe)}")
    return 0 if (ffmpeg and ffprobe) else 1


if __name__ == "__main__":
    sys.exit(main())
