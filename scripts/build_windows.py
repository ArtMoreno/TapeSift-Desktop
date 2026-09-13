"""Build a Windows executable with PyInstaller.

Usage:
    python -m venv .venv
    .venv\\Scripts\\pip install -r requirements.txt pyinstaller
    .venv\\Scripts\\python scripts/build_windows.py

Output lands in dist/TapeSift/TapeSift.exe.

Two things here are load-bearing and easy to break:

1. **Do not use --collect-submodules PySide6.** It drags in the whole of Qt
  , WebEngine alone is 196 MB, plus QML, 3D, Charts and every translation.
   That produced a 624 MB build of which TapeSift used maybe a tenth. The
   EXCLUDES list below is what makes the build ~90 MB instead.

2. **The multimedia plugins must survive the trim.** Playback is the entire
   product; if `PySide6/plugins/multimedia/` goes missing the app still
   starts and simply never shows a frame. verify_build() fails the build
   rather than let that ship silently.

FFmpeg is bundled from vendor/ffmpeg when it is present, run
scripts/fetch_ffmpeg.py once to populate it. Without it the build still
works and falls back to the user's own FFmpeg, but that is a developer
build, not a shippable one, so the build says so loudly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapesift import (  # noqa: E402
    AUTHOR_LEGAL, COPYRIGHT_LINE, __version__ as VERSION,
)
from scripts.build_brand_assets import build_assets  # noqa: E402

# One shipped application: the alpha build. Default matches the app name.
NAME = "TapeSift"

DIST = ROOT / "dist" / NAME
VENDOR_FFMPEG = ROOT / "vendor" / "ffmpeg"

# Qt modules TapeSift never imports. Each one costs tens of megabytes.
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtSerialBus", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtRemoteObjects", "PySide6.QtTextToSpeech", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer",
    # Scientific stack that ships with other tooling and is never imported.
    "numpy", "pandas", "matplotlib", "scipy", "PIL", "tkinter",
    # Developer-only unauthenticated LAN tool. SECURITY.md documents why it
    # must not be reachable from the shipped product.
    "tapesift.ui_v2.companion_dialog", "tapesift.services.companion_server",
]

# Directories the trim removes outright. Translations are ~58 MB of locales
# for an English-only app; qml/resources belong to the QML and WebEngine
# stacks that are already excluded.
PRUNE_DIRS = ["translations", "qml", "resources"]

# QtCore on Windows uses the OS ICU shim. PyInstaller can accidentally collect
# an unrelated ICU build from the developer shell's PATH; that shadows the OS
# DLL and makes the frozen app fail before its first window is created.
PRUNE_ROOT_DLLS = ["icuuc.dll", "icudt*.dll"]

# Files that must exist afterwards or the app is broken in a way that only
# shows up as "the video never plays".
REQUIRED = [
    f"{NAME}.exe",
    "THIRD_PARTY_NOTICES.txt",
    "_internal/tapesift/resources/teams/catalog.json",
    "_internal/tapesift/resources/images/tag-map-turf.png",
    "_internal/tapesift/resources/branding/broadcast-slate-texture.png",
    "_internal/tapesift/resources/branding/ifi-full-name.jpg",
    "_internal/tapesift/resources/branding/ifi-horizontal-approved.jpg",
    "_internal/tapesift/resources/branding/home-field.png",
    "_internal/tapesift/resources/branding/more-menus.png",
    "_internal/tapesift/resources/icons/v3/find-snap-football-24.svg",
    "_internal/PySide6/plugins/multimedia/windowsmediaplugin.dll",
    "_internal/PySide6/plugins/platforms/qwindows.dll",
    "_internal/tapesift/resources/transport/transport-machined-play-2x.png",
    "_internal/tapesift/resources/transport/transport-machined-pause-2x.png",
]

# PyInstaller 6.21 places Qt's linked DLLs at ``_internal`` while older
# releases kept them inside ``_internal/PySide6``.  Both layouts are valid;
# the plugin directories above remain under PySide6 in either layout.
REQUIRED_ANY = [
    (
        "_internal/Qt6Multimedia.dll",
        "_internal/PySide6/Qt6Multimedia.dll",
    ),
    (
        "_internal/Qt6MultimediaWidgets.dll",
        "_internal/PySide6/Qt6MultimediaWidgets.dll",
    ),
]


def is_lgpl_ffmpeg_build(version_output: str) -> bool:
    """The shipped notice is valid only for our LGPL v3 FFmpeg build."""
    configuration = next(
        (line for line in version_output.splitlines()
         if line.startswith("configuration:")),
        "",
    )
    return (
        "--enable-version3" in configuration
        and "--enable-gpl" not in configuration
        and "--enable-nonfree" not in configuration
    )


def install_third_party_notices() -> bool:
    """Put notices beside the executable and record the exact FFmpeg build."""
    notice = ROOT / "installer" / "THIRD_PARTY_NOTICES.txt"
    shutil.copy2(notice, DIST / notice.name)

    bundled_ffmpeg = DIST / "_internal" / "ffmpeg" / "ffmpeg.exe"
    if not bundled_ffmpeg.is_file():
        return True
    try:
        completed = subprocess.run(
            [str(bundled_ffmpeg), "-version"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"  COULD NOT VERIFY FFMPEG LICENCE: {exc}")
        return False

    version_output = completed.stdout
    if not is_lgpl_ffmpeg_build(version_output):
        print(
            "  LICENCE MISMATCH: bundled FFmpeg must use --enable-version3 "
            "without --enable-gpl or --enable-nonfree.")
        return False
    (DIST / "FFMPEG_BUILD_INFO.txt").write_text(
        version_output, encoding="utf-8")
    return True


def write_version_resource(target: Path) -> Path:
    """Windows file-properties metadata for TapeSift.exe.

    Without this the executable's Details tab is blank, no product name,
    no author, no copyright, which looks like malware to a cautious user
    and is the first thing anyone checks on an unsigned download.
    """
    # Windows requires a numeric four-part file version even when the product
    # label is a prerelease such as ``0.7.0-alpha``.
    parts = []
    for component in VERSION.split(".")[:3]:
        digits = "".join(char for char in component if char.isdigit())
        parts.append(int(digits or 0))
    parts += [0] * (4 - len(parts))
    quad = ", ".join(str(x) for x in parts[:4])
    target.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({quad}), prodvers=({quad}), mask=0x3f,
                    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[
    StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', {AUTHOR_LEGAL!r}),
        StringStruct('FileDescription', 'TapeSift football film clipper'),
        StringStruct('FileVersion', {VERSION!r}),
        StringStruct('InternalName', 'TapeSift'),
        StringStruct('LegalCopyright', {COPYRIGHT_LINE!r}),
        StringStruct('OriginalFilename', '{NAME}.exe'),
        StringStruct('ProductName', '{NAME}'),
        StringStruct('ProductVersion', {VERSION!r}),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    return target


def build() -> int:
    (ROOT / "build").mkdir(exist_ok=True)
    version_file = write_version_resource(ROOT / "build" / "version_info.txt")
    # Assembled as a flat list of flags, then the script last. An earlier
    # version spliced options in at a fixed index, which silently separated
    # --version-file from its value once another flag moved.
    options = [
        "--name", NAME,
        "--version-file", str(version_file),
        "--windowed",
        "--noconfirm",
        "--clean",
    ]
    icon = ROOT / "tapesift" / "resources" / "icons" / "tapesift.ico"
    if icon.exists():
        options += ["--icon", str(icon)]
    fonts = ROOT / "tapesift" / "resources" / "fonts"
    if fonts.is_dir():
        options += ["--add-data", f"{fonts}{os.pathsep}tapesift/resources/fonts"]
    icons = ROOT / "tapesift" / "resources" / "icons"
    if icons.is_dir():
        options += ["--add-data", f"{icons}{os.pathsep}tapesift/resources/icons"]
    images = ROOT / "tapesift" / "resources" / "images"
    if images.is_dir():
        options += ["--add-data", f"{images}{os.pathsep}tapesift/resources/images"]
    transport = ROOT / "tapesift" / "resources" / "transport"
    if transport.is_dir():
        options += [
            "--add-data",
            f"{transport}{os.pathsep}tapesift/resources/transport",
        ]
    branding = ROOT / "tapesift" / "resources" / "branding"
    if branding.is_dir():
        options += ["--add-data", f"{branding}{os.pathsep}tapesift/resources/branding"]
    # Bundled squads. Loose data ships only because it is named here - the
    # app reads it fine from source either way, so a missing line shows up
    # as an exe with an empty roster and no error at all.
    rosters = ROOT / "tapesift" / "resources" / "rosters"
    if rosters.is_dir():
        options += [
            "--add-data",
            f"{rosters}{os.pathsep}tapesift/resources/rosters"]
    teams = ROOT / "tapesift" / "resources" / "teams"
    if teams.is_dir():
        options += ["--add-data", f"{teams}{os.pathsep}tapesift/resources/teams"]
    for module in EXCLUDES:
        options += ["--exclude-module", module]
    # Bundled FFmpeg lands in _internal/ffmpeg/, where bundled_dir() looks.
    if VENDOR_FFMPEG.is_dir():
        for binary in sorted(VENDOR_FFMPEG.glob("*")):
            options += ["--add-binary", f"{binary}{os.pathsep}ffmpeg"]
    # Shell V3 is the shipped application.  The V2 source entry remains
    # available through the explicit rollback shortcut, but freezing it here
    # would silently turn a successful build back into the retired shell.
    cmd = [sys.executable, "-m", "PyInstaller", *options,
           str(ROOT / "run_tapesift_v3.py")]
    print("Running PyInstaller…")
    return subprocess.call(cmd, cwd=ROOT)


def prune() -> None:
    qt = DIST / "_internal" / "PySide6"
    for name in PRUNE_DIRS:
        target = qt / name
        if target.is_dir():
            shutil.rmtree(target)
            print(f"  pruned PySide6/{name}")
    for pattern in PRUNE_ROOT_DLLS:
        for target in (DIST / "_internal").glob(pattern):
            target.unlink()
            print(f"  pruned accidental runtime DLL {target.name}")


def verify() -> bool:
    """A build that starts but can't play video is worse than a failed one."""
    required = list(REQUIRED)
    required += [
        f"_internal/tapesift/resources/teams/{path.name}"
        for path in (ROOT / "tapesift" / "resources" / "teams").glob("*.png")
    ]
    if VENDOR_FFMPEG.is_dir():
        required += [
            "FFMPEG_BUILD_INFO.txt",
            "_internal/ffmpeg/ffmpeg.exe",
            "_internal/ffmpeg/ffprobe.exe",
        ]
    missing = [p for p in required if not (DIST / p).exists()]
    for alternatives in REQUIRED_ANY:
        if not any((DIST / path).exists() for path in alternatives):
            missing.append(" or ".join(alternatives))
    if VENDOR_FFMPEG.is_dir() and not list(
            (DIST / "_internal" / "ffmpeg").glob("FFmpeg-source-*.zip")):
        missing.append("_internal/ffmpeg/FFmpeg-source-<revision>.zip")
    for path in missing:
        print(f"  MISSING: {path}")
    return not missing


def folder_mb(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) // 2**20


def main() -> int:
    print("Rendering canonical TapeSift brand assets...")
    build_assets()
    code = build()
    if code != 0:
        return code
    print(f"Built: {folder_mb(DIST)} MB")
    prune()
    if not install_third_party_notices():
        print("BUILD REJECTED, FFmpeg licence configuration is not approved.")
        return 1
    if not verify():
        print("BUILD REJECTED, required Qt components are missing.")
        return 1
    print(f"Trimmed: {folder_mb(DIST)} MB")
    if not VENDOR_FFMPEG.is_dir():
        print("\n*** NOT SHIPPABLE: no bundled FFmpeg. This build relies on "
              "the user already having FFmpeg installed.")
        print("*** Run scripts/fetch_ffmpeg.py, then build again.")
    print(f"\nDone: {DIST / f'{NAME}.exe'}")
    print("Smoke-test it before shipping: load a video and press play.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
