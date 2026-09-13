"""Source video metadata extracted via FFprobe."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class VideoMetadata:
    path: str = ""
    container: str = ""
    duration_ms: int = 0
    file_size_bytes: int = 0
    width: int = 0
    height: int = 0
    frame_rate: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    stream_count: int = 0
    time_base: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "unknown"

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "VideoMetadata":
        if not raw:
            return cls()
        data = json.loads(raw)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
