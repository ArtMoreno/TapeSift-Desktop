"""Build the shippable TapeSift installer.

    .venv\\Scripts\\python scripts/build_installer.py [--skip-freeze]

Freezes the app (scripts/build_windows.py), then compiles
installer/tapesift.iss with Inno Setup into dist/installer/.

Install Inno Setup once:  winget install JRSoftware.InnoSetup

The version comes from tapesift/__init__.py and is passed to the script,
so there is exactly one place to bump it.

The result is NOT signed. Windows SmartScreen will warn on download until
a code-signing certificate is applied, see the shipping notes in README.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapesift import __version__ as VERSION  # noqa: E402

ISS = ROOT / "installer" / "tapesift.iss"
FROZEN = ROOT / "dist" / "TapeSift" / "TapeSift.exe"
OUT_DIR = ROOT / "dist" / "installer"

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_iscc() -> Path | None:
    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup not found. Install it with:")
        print("    winget install JRSoftware.InnoSetup")
        return 1

    if "--skip-freeze" not in sys.argv:
        code = subprocess.call([sys.executable, str(ROOT / "scripts" /
                                                    "build_windows.py")])
        if code != 0:
            return code
    if not FROZEN.is_file():
        print(f"No frozen build at {FROZEN}. Run without --skip-freeze.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nCompiling installer for {VERSION}…")
    code = subprocess.call([str(iscc), f"/DAppVersion={VERSION}", str(ISS)],
                           cwd=ISS.parent)
    if code != 0:
        return code

    setup = OUT_DIR / f"TapeSiftSetup-{VERSION}.exe"
    if not setup.is_file():
        print(f"Inno Setup reported success but {setup.name} is missing.")
        return 1
    print(f"\nDone: {setup}")
    print(f"      {setup.stat().st_size / 2**20:.0f} MB")
    print("\nUNSIGNED, SmartScreen will warn on download until a "
          "code-signing certificate is applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
