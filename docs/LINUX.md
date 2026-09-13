# Linux and Omarchy

The application uses the same Qt interface and project format on both platforms. Omarchy uses Arch Linux and a Wayland desktop; see the [Omarchy manual](https://omarchy.org/manual/omarchy-on/). Qt supports both [Wayland](https://doc.qt.io/qt-6/wayland-requirements.html) and X11.

## Arch / Omarchy dependencies

```sh
sudo pacman -S --needed python python-pip ffmpeg libxcb libxkbcommon-x11 \
  xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil \
  xcb-util-wm libglvnd libpulse alsa-lib
```

For optional saved First Read credentials, install `gnome-keyring` or another compatible Secret Service provider and unlock its login collection. The Python dependency uses the [Secret Service backend](https://keyring.readthedocs.io/en/latest/) explicitly, with no plaintext fallback.

## Ubuntu dependencies

```sh
sudo apt install python3-venv ffmpeg libegl1 libopengl0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-render-util0 libxcb-xinerama0 libpulse0 libasound2t64
```

Create a virtual environment and install the application as shown in the README. Do not use the Windows FFmpeg download on Linux; use the distribution's `ffmpeg` and `ffprobe` executables.

## Desktop integration

```sh
.venv/bin/python scripts/install_linux_desktop.py
```

This writes a `tapesift.desktop` entry under `$XDG_DATA_HOME/applications` (normally `~/.local/share/applications`). It references this checkout and virtual environment. Re-run the command after moving the checkout. It does not change desktop shortcuts or compositor bindings.

Qt chooses the available display backend. If the compositor reports a Qt plugin problem, try `QT_QPA_PLATFORM=xcb .venv/bin/python -m tapesift` for an XWayland comparison. Keep the log from the failed Wayland launch when reporting the issue.

## Data and moving projects

New Linux profiles use `$XDG_DATA_HOME/tapesift` (normally `~/.local/share/tapesift`). Existing `~/.tapesift` profiles continue to load. Projects and exports use the home Documents and Videos folders by default and can be changed in Settings. Windows keeps its existing data locations.

Copy a closed project file and its source footage to Linux. Open the project and relink the source if its old path is unavailable. Windows credential protection is machine-specific; enter the API key again on Linux if using First Read.

## Older laptops

For a ThinkPad T480s, start with one short local film and enable smooth-scrub preview generation. Store previews on the SSD and let a build finish before judging playback. Compare normal playback, J/K/L reversal, frame stepping, repeated pause/resume, and one export. Original footage remains the export source.

Laptop GPU, codec, thermals, memory, and storage affect results. Linux startup or CI passing does not establish smooth editing on this hardware. Record that acceptance check on the actual laptop before relying on it for a full session.
