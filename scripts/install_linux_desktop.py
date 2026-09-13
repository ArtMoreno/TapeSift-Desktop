"""Install a desktop launcher for this checkout and Python environment."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def install(data_home: Path | None = None) -> Path:
    if sys.platform != "linux":
        raise SystemExit("This launcher installer is for Linux.")
    root = Path(__file__).resolve().parents[1]
    destination = (data_home or Path(os.environ.get(
        "XDG_DATA_HOME", Path.home() / ".local/share"))) / "applications/tapesift.desktop"
    destination.parent.mkdir(parents=True, exist_ok=True)
    def quoted(value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Launcher paths cannot contain line breaks")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace(
            "`", "\\`").replace("$", "\\$").replace("%", "%%") + '"'
    command = f"{quoted(sys.executable)} {quoted(str(root / 'run_tapesift_v3.py'))} %f"
    destination.write_text(
        "[Desktop Entry]\nType=Application\nName=TapeSift\n"
        "Comment=Football film review and clip editing\n"
        f"Exec={command}\nIcon={root / 'tapesift/resources/brand/tapesift-app-icon.svg'}\n"
        "Terminal=false\nCategories=AudioVideo;Video;\nStartupWMClass=TapeSift\n",
        encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(install())
