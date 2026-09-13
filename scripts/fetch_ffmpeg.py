"""Download the FFmpeg build that ships inside TapeSift.

    python scripts/fetch_ffmpeg.py

Lands ffmpeg.exe, ffprobe.exe and the licence text in vendor/ffmpeg/,
which is gitignored, binaries do not belong in the repo, so anyone
building from a clean checkout runs this once.

**Why the LGPL build specifically.** Gyan's "full_build" (what this
machine has on PATH) is GPL: shipping it would put TapeSift's own source
under the GPL's terms. The LGPL build omits the GPL-only pieces, x264,
x265 and a handful of filters TapeSift never invokes, and can be
redistributed alongside closed source. TapeSift encodes H.264 through
libopenh264 or the hardware encoders, neither of which is affected.

Get the licensing reviewed before you actually sell this. The choice here
is the conservative one, not legal advice.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "ffmpeg"

# Pinned to a release tag, never "latest": the FFmpeg that ships must be
# the FFmpeg that was tested. Bumping this is a deliberate act.
URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
       "autobuild-2026-07-31-14-10/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-lgpl-7.1.zip")
SHA256 = "b7c1c846dacca68ee4ebf5c390742c973b3d5d14a6d44b061f500d8e4ac74fc0"
WANTED = ("ffmpeg.exe", "ffprobe.exe", "LICENSE.txt")


def download(url: str) -> bytes:
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as response:
        data = response.read()
    print(f"  {len(data) / 2**20:.1f} MB, sha256 "
          f"{hashlib.sha256(data).hexdigest()[:16]}…")
    return data


def extract(data: bytes) -> list[str]:
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir(parents=True)
    written = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.namelist():
            name = Path(entry).name
            if name in WANTED and not entry.endswith("/"):
                target = VENDOR / name
                with archive.open(entry) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                written.append(name)
                print(f"  wrote vendor/ffmpeg/{name}")
    return written


def bundle_corresponding_source() -> str:
    """Download the exact FFmpeg revision reported by the bundled binary."""
    ffmpeg = VENDOR / "ffmpeg.exe"
    completed = subprocess.run(
        [str(ffmpeg), "-version"],
        capture_output=True,
        text=True,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    match = re.search(
        r"ffmpeg version .*?-g([0-9a-f]{7,40})(?:-|\s)",
        completed.stdout.splitlines()[0],
    )
    if not match:
        raise RuntimeError(
            "Could not read the FFmpeg source revision from ffmpeg -version.")
    revision = match.group(1)
    source_url = (
        f"https://github.com/FFmpeg/FFmpeg/archive/{revision}.zip")
    source_name = f"FFmpeg-source-{revision}.zip"
    (VENDOR / source_name).write_bytes(download(source_url))
    print(f"  wrote vendor/ffmpeg/{source_name}")
    return source_name


def main() -> int:
    data = download(URL)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("FFmpeg download checksum mismatch; existing vendor files were not changed.")
    written = extract(data)
    missing = [n for n in ("ffmpeg.exe", "ffprobe.exe") if n not in written]
    if missing:
        print(f"FAILED, archive did not contain: {', '.join(missing)}")
        return 1
    try:
        bundle_corresponding_source()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"FAILED to bundle corresponding FFmpeg source: {exc}")
        return 1
    print(f"\nDone: {VENDOR}")
    print("Rebuild with scripts/build_windows.py to bundle it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
