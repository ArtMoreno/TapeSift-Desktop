"""Application settings, persisted as JSON in the user's app-data folder."""

from __future__ import annotations

import base64
import contextlib
import ctypes
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from ctypes import wintypes

from tapesift.core import paths

log = logging.getLogger(__name__)

_PROTECTED_API_KEY = "first_read_api_key_dpapi"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi_transform(value: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable on this platform")
    buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(
        len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "TapeSift OpenRouter API key", None, None,
            None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result))
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _protect_api_key(value: str) -> str:
    if sys.platform.startswith("linux"):
        reference = "keyring:" + uuid.uuid4().hex
        try:
            _linux_keyring().set_password("TapeSift First Read", reference, value)
        except Exception as exc:
            raise OSError("Unlock a Secret Service keyring to save the API key.") from exc
        return reference
    encrypted = _dpapi_transform(value.encode("utf-8"), protect=True)
    return base64.b64encode(encrypted).decode("ascii")


def _unprotect_api_key(value: str) -> str:
    if value.startswith("keyring:"):
        try:
            result = _linux_keyring().get_password("TapeSift First Read", value)
        except Exception as exc:
            raise OSError("The Secret Service keyring is unavailable or locked.") from exc
        if result is None:
            raise OSError("The saved API key is missing from the keyring.")
        return result
    encrypted = base64.b64decode(value, validate=True)
    return _dpapi_transform(encrypted, protect=False).decode("utf-8")


def _linux_keyring():
    # Select Secret Service explicitly; never fall back to a plaintext backend.
    from keyring.backends.SecretService import Keyring
    return Keyring()


def _remove_key_reference(reference: str) -> None:
    if reference.startswith("keyring:"):
        try:
            _linux_keyring().delete_password("TapeSift First Read", reference)
        except Exception:
            log.warning("Could not remove an unused keyring entry")

SEPARATOR_HYPHEN = "hyphen"
SEPARATOR_UNDERSCORE = "underscore"

DEFAULT_NAMING_TEMPLATE = "{clip_number}_{clip_name}"

# When to ask for play details. Football rhythm: mark the snap, watch the
# play, then log it - so the default prompts after the out-point, when the
# result and the players involved are actually known.
DETAILS_AFTER_OUT = "after_out"
DETAILS_AT_IN = "at_in"
DETAILS_OFF = "off"

INSPECTOR_DENSITY_COMPACT = "compact"
INSPECTOR_DENSITY_COMFORTABLE = "comfortable"
INSPECTOR_DENSITY_SPACIOUS = "spacious"


def _default_fixed_details() -> dict[str, list[str]]:
    from tapesift.services.football_vocab import default_fixed_details
    return default_fixed_details()


@dataclass
class AppSettings:
    # General
    default_project_folder: str = ""
    shared_projects_folder: str = ""
    shared_project_links: dict[str, dict[str, str]] = field(default_factory=dict)
    default_output_folder: str = ""
    autosave_interval_seconds: int = 30
    recent_project_count: int = 10
    # No `theme` field. Settings offered Dark/Light and stored the choice;
    # nothing read it, so Light did nothing. `load` ignores unknown keys, so
    # dropping it leaves an existing settings.json readable.
    confirm_before_delete: bool = True
    recent_projects: list[str] = field(default_factory=list)
    recent_videos: list[str] = field(default_factory=list)
    # False until the welcome screen has been shown once. New installs get
    # an empty home screen otherwise, with nothing explaining what the app
    # expects or what to do first.
    onboarding_seen: bool = False

    # One-key workflow: after saving a clip, hand focus (and optionally
    # playback) straight back to the transport.
    resume_after_save: bool = True
    # Remembered from the last export so dialogs open where you last were.
    last_export_folder: str = ""

    # Review mode: stepping through clips and logging them one after another.
    review_mode: bool = False
    review_autoplay: bool = True       # play each clip on landing
    review_auto_advance: bool = True   # saving details jumps to the next clip
    review_loop: bool = False          # repeat the clip until you move on

    # Playback
    jump_backward_seconds: float = 3.0
    jump_forward_seconds: float = 3.0
    default_playback_speed: float = 1.0
    volume: int = 80
    # Voiceover input devices can be re-enumerated by Windows after a USB or
    # Bluetooth reconnect. Resolve the stable ID first and use the saved
    # description only as a fallback.
    voiceover_input_device_id: str = ""
    voiceover_input_device_name: str = ""
    # Auto-build a scrub-optimized preview copy when a video loads.
    # A long film still costs CPU/GPU time and cache space; Playback settings
    # can disable automatic builds. Existing preview copies are always reused.
    scrub_proxy_enabled: bool = True

    # Clip defaults
    default_pre_roll_seconds: float = 5.0
    default_post_roll_seconds: float = 8.0
    separator_style: str = SEPARATOR_HYPHEN
    warn_duplicate_names: bool = True
    # When the Clip Details popover appears: after_out | at_in | off
    details_prompt: str = DETAILS_AFTER_OUT
    pause_on_details: bool = False
    # Fixed dropdowns: details/tags offer a predefined list instead of
    # learning values as you type. Lists are editable in Settings.
    use_fixed_dropdowns: bool = True
    fixed_tags: list[str] = field(default_factory=lambda: [
        "Pass TD", "Rushing TD", "Catch", "3rd Down", "4th Down", "Red Zone",
        "Interception", "Sack", "Pressure", "Turnover", "Explosive Play",
        "Penalty", "Big Hit", "Missed Tackle",
    ])
    #: Your team, used to work out the opponent from a film's filename.
    my_team: str = ""
    #: Compact clip list: no thumbnail column, tighter rows, more on screen.
    clip_list_compact: bool = False
    #: New clips slot into the list by their start time rather than landing
    #: at the bottom, so the list always mirrors the game.
    insert_chronologically: bool = True
    # This changes inspector spacing only, never clip fields or project data.
    inspector_density: str = INSPECTOR_DENSITY_COMPACT
    # Presentation-only timeline grouping. Clip metadata is never rewritten
    # when this changes.
    timeline_color_by: str = "play_type"
    # Versioned desktop-workspace state. Qt byte arrays are encoded as Base64
    # by the V2 UI layer so this settings model remains toolkit-independent.
    workspace_geometry_v1: str = ""
    workspace_state_v1: str = ""
    workspace_player_visible: bool = True
    workspace_clips_visible: bool = True
    workspace_play_details_visible: bool = True
    #: Folded to a strip rather than closed. Kept apart from
    #: _visible because a folded panel is still open - it just has
    #: handed its width to the video while you log in the grid.
    workspace_play_details_collapsed: bool = False
    # Reopen the last window size, maximized state, and dock layout.
    # Off by default: the workspace opens clean and evenly split every
    # time. Restoring also replayed a stale maximized flag, which paints
    # the frameless shell's square (maximized) corners on launch.
    workspace_restore_layout: bool = False
    #: The Tag Map keeps a visible header when folded so the reclaimed
    #: vertical space belongs to the film and the panel is always recoverable.
    workspace_tag_map_collapsed: bool = False
    show_tag_map_labels: bool = True
    # Visibility only; hidden rows never delete saved clip metadata.
    hidden_tag_map_rows: list[str] = field(default_factory=lambda: ["action"])
    #: Bundled squad offered in the player field when a project has
    #: no roster.csv of its own.
    default_roster: str = "miami_2026"
    # Native floating windows remember maximized independently from Qt's
    # dock topology. Minimized and true-fullscreen states are intentionally
    # transient so a restart can never strand an apparently missing panel.
    workspace_maximized_docks_v1: list[str] = field(default_factory=list)
    timeline_follow_playhead: bool = True
    # Unclassified detector footage is preserved in the coverage ledger but
    # stays visually restrained unless the user explicitly asks to see it.
    timeline_show_ignored_fragments: bool = False
    # Play Details presentation preferences. Empty order means the built-in
    # order. Internal field keys remain stable even when labels are changed.
    detail_field_order: list[str] = field(default_factory=list)
    hidden_detail_fields: list[str] = field(default_factory=list)
    detail_field_labels: dict[str, str] = field(default_factory=dict)
    # Empty means the built-in quick-tag favorites and their default order.
    # Once customized, the complete list is stored here so custom actions,
    # labels, removals, and ordering survive an application restart.
    quick_tag_favorites: list[dict[str, object]] = field(default_factory=list)
    # Result chips are deliberately separate from the full result library:
    # the inspector shows at most six favorites while More Results preserves
    # every standard, project-learned, and user-created value.
    result_favorites: list[str] = field(default_factory=list)
    # None retains V3 defaults; [] intentionally hides all sixteen shortcuts.
    v3_result_favorites: list[str] | None = None
    action_shortcuts: dict[str, list[str]] = field(default_factory=dict)
    # First Read is the one feature that contacts the network, so it is off
    # until the user turns it on and supplies their own key. The key is held
    # here, in the application settings, and never written into a project, an
    # export, or a log - a project file is the thing people send each other.
    #
    # Persisted with user-scoped Windows DPAPI; never serialized in plaintext.
    first_read_enabled: bool = False
    first_read_api_key: str = ""
    # Empty means the shipped default model.
    first_read_model: str = ""
    # Preferred height of the resizable Notes editor in the V2 Library.
    library_notes_height: int = 72
    explosive_rush_yards: int = 12
    explosive_pass_yards: int = 16
    fixed_details: dict[str, list[str]] = field(
        default_factory=lambda: _default_fixed_details())
    # Removing an offered choice must survive reseeding newly shipped choices.
    hidden_fixed_details: dict[str, list[str]] = field(default_factory=dict)

    # Export
    default_preset: str = "source_quality"
    accurate_cut: bool = True
    naming_template: str = DEFAULT_NAMING_TEMPLATE
    output_organization: str = "structured"  # structured | flat | by_label | by_tag | by_preset
    overwrite_behavior: str = "unique"  # unique | overwrite | ask
    hardware_acceleration: str = "cpu"

    # FFmpeg
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    # True only when the user picked these in the setup dialog. An
    # auto-detected path is a cache, not a decision: without this flag a
    # remembered detection outranks the FFmpeg we ship, so an installed
    # build would keep using whatever happened to be on the machine.
    ffmpeg_path_manual: bool = False

    def save(self, path: Path | None = None) -> None:
        """Write settings atomically: whole new file, or the old one intact.

        write_text truncates first and then writes. Losing power, or being
        killed by the native crash this app still has, in between left a
        half-written settings.json - and `load` treats unparseable JSON as
        "no settings", so every preference silently reset. Writing beside
        the target and renaming means a reader only ever sees one complete
        version; os.replace is atomic on Windows and POSIX both.
        """
        target = path or paths.settings_file()
        old_reference = ""
        try:
            previous = json.loads(target.read_text(encoding="utf-8-sig"))
            old_reference = previous.get(_PROTECTED_API_KEY, "")
        except (OSError, ValueError, AttributeError):
            pass
        data = asdict(self)
        api_key = data.pop("first_read_api_key")
        if api_key:
            data[_PROTECTED_API_KEY] = _protect_api_key(api_key)
        payload = json.dumps(data, indent=2)
        # Same directory as the target: os.replace cannot be atomic across
        # volumes, and the temp dir is routinely on another one.
        tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            _remove_key_reference(data.get(_PROTECTED_API_KEY, ""))
            raise
        if isinstance(old_reference, str):
            _remove_key_reference(old_reference)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        target = path or paths.settings_file()
        settings = cls()
        legacy_key = None
        if target.exists():
            try:
                # utf-8-sig: tolerate a byte-order mark, which some editors and
                # PowerShell add. A BOM used to make settings silently reset.
                data = json.loads(target.read_text(encoding="utf-8-sig"))
                known = {f for f in settings.__dataclass_fields__}
                for key, value in data.items():
                    if key in known and key != "first_read_api_key":
                        setattr(settings, key, value)
                protected_key = data.get(_PROTECTED_API_KEY)
                if isinstance(protected_key, str) and protected_key:
                    try:
                        settings.first_read_api_key = _unprotect_api_key(
                            protected_key)
                    except (OSError, ValueError, UnicodeError) as exc:
                        log.warning(
                            "Could not decrypt OpenRouter API key: %s", exc)
                legacy_key = data.get("first_read_api_key")
                if (not settings.first_read_api_key
                        and isinstance(legacy_key, str)):
                    settings.first_read_api_key = legacy_key
                # Migrate the old two-state I-key flag.
                if "details_prompt" not in data and "i_key_guided" in data:
                    settings.details_prompt = (
                        DETAILS_AFTER_OUT if data["i_key_guided"] else DETAILS_OFF)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Could not read settings file %s: %s", target, exc)
        if not settings.default_project_folder:
            settings.default_project_folder = str(paths.default_projects_dir())
        if not settings.default_output_folder:
            settings.default_output_folder = str(paths.default_exports_dir())
        settings._repoint_stale_system_drive_defaults()
        settings._heal_football_vocab()
        if target.exists() and isinstance(legacy_key, str):
            try:
                settings.save(target)
            except OSError as exc:
                log.warning("Could not migrate OpenRouter API key: %s", exc)
        return settings

    def _heal_football_vocab(self) -> None:
        """Reseed shipped football lists; keep user-added extras."""
        from tapesift.services.football_vocab import (
            DEFAULT_EXPLOSIVE_PASS_YARDS, DEFAULT_EXPLOSIVE_RUSH_YARDS,
            heal_fixed_details,
        )
        if not isinstance(self.fixed_details, dict):
            self.fixed_details = {}
        if not isinstance(self.hidden_fixed_details, dict):
            self.hidden_fixed_details = {}
        self.hidden_fixed_details = {
            key: values for key, values in self.hidden_fixed_details.items()
            if isinstance(values, list)}
        self.fixed_details = heal_fixed_details(
            self.fixed_details, self.hidden_fixed_details)
        if not isinstance(self.explosive_rush_yards, int):
            self.explosive_rush_yards = DEFAULT_EXPLOSIVE_RUSH_YARDS
        if not isinstance(self.explosive_pass_yards, int):
            self.explosive_pass_yards = DEFAULT_EXPLOSIVE_PASS_YARDS

    def _repoint_stale_system_drive_defaults(self) -> None:
        """Move untouched defaults off the system drive onto the data drive.

        Early builds defaulted projects to Documents and exports to Videos,
        both on C:. On a machine where C: is nearly full and D: has the room,
        that default is actively harmful - and a user who never opened
        Settings has no idea it is why they ran out of space.

        Only a folder still sitting on the old default is moved, and only the
        *setting* moves: no files are touched, and existing projects keep
        opening from wherever they already are. A folder the user chose
        themselves is left exactly as they set it.
        """
        moves = (
            ("default_project_folder",
             paths.legacy_projects_dir(), paths.default_projects_dir),
            ("default_output_folder",
             paths.legacy_exports_dir(), paths.default_exports_dir),
        )
        for field, legacy, new_default in moves:
            current = getattr(self, field, "") or ""
            if not current:
                continue
            try:
                unchanged = Path(current) == legacy
            except (OSError, ValueError):
                continue
            if not unchanged:
                continue          # the user picked this - leave it alone
            replacement = new_default()
            if replacement == legacy:
                continue          # no data drive available; nothing to do
            setattr(self, field, str(replacement))
            log.info(
                "Moved default %s off the system drive: %s -> %s",
                field, legacy, replacement)

    def prune_missing_recents(self) -> int:
        """Drop recent entries whose project file is really gone.

        A recent card that cannot possibly open is worse than no card: it
        reads as "opening projects is broken" rather than "this file moved".

        "Really gone" is doing work here. Projects live on the data drive,
        and an external or temporarily unavailable volume would make every
        path on it look missing - pruning then would silently erase the
        whole recents list over a drive that comes back a minute later. So
        an entry is only dropped when its drive is present and the file
        still is not: a missing file on a mounted volume is a deletion, a
        missing volume is not our business.
        """
        keep: list[str] = []
        removed = 0
        for entry in self.recent_projects:
            try:
                path = Path(entry)
                anchor = path.anchor
                volume_present = (not anchor) or Path(anchor).exists()
                gone = volume_present and not path.is_file()
            except OSError:
                gone = False        # unreadable is not the same as absent
            if gone:
                removed += 1
                log.info("Dropping recent project, file is gone: %s", entry)
            else:
                keep.append(entry)
        if removed:
            self.recent_projects[:] = keep
        return removed

    def add_recent_project(self, db_path: str) -> None:
        self._push_recent(self.recent_projects, db_path)

    def add_recent_video(self, video_path: str) -> None:
        self._push_recent(self.recent_videos, video_path)

    def _push_recent(self, bucket: list[str], item: str) -> None:
        if item in bucket:
            bucket.remove(item)
        bucket.insert(0, item)
        del bucket[self.recent_project_count:]
