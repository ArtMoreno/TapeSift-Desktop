"""Project session: owns the open project, its clips, persistence, and undo/redo."""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tapesift.core.exceptions import DatabaseError
from tapesift.database.autodetect_repository import (
    AutodetectRepository, new_capture_id, utc_now,
)
from tapesift.database.connection import PROJECT_FILE_EXTENSION, open_project_db
from tapesift.database.repositories import ClipRepository, ProjectRepository
from tapesift.models.clip import Clip
from tapesift.models.project import Project
from tapesift.services import coverage_audit_service, detail_service
from tapesift.services.autodetect_capture_service import selected_candidate_keys
from tapesift.services.duplicate_service import metadata_score
from tapesift.services.play_detect_service import REVIEW_PREFIX, REVIEW_TAG
from tapesift.services.snap_prediction_service import PREDICTION_KEY

log = logging.getLogger(__name__)

MAX_UNDO_STEPS = 100
MIN_CLIP_DURATION_MS = 250


@dataclass
class _Snapshot:
    """Undoable state: the clip list (deep copy)."""
    clips: list[Clip]
    description: str
    naming_template: str | None = None
    logging_defaults: dict | None = None


@dataclass(frozen=True)
class FragmentReclaimOption:
    """One safe way to absorb preserved detector footage into a play."""

    segment_index: int
    clip_id: str
    edge: str
    fragment_start_ms: int
    fragment_end_ms: int
    original_boundary_ms: int
    target_boundary_ms: int
    blocker_clip_id: str = ""
    blocker_title: str = ""

    @property
    def reclaimed_ms(self) -> int:
        return abs(self.target_boundary_ms - self.original_boundary_ms)


def rename_project(db_path: Path, new_name: str) -> Path:
    """Rename a closed project: its file on disk and its stored name.

    Returns the new path. The caller is responsible for updating anything
    that referenced the old path (recent list, library index).
    """
    new_name = new_name.strip()
    if not new_name:
        raise DatabaseError("A project name cannot be empty.")
    invalid = set('<>:"/\\|?*')
    if invalid & set(new_name):
        raise DatabaseError(
            'A project name cannot contain < > : " / \\ | ? *',
            "Choose a name without those characters.")
    if not db_path.is_file():
        raise DatabaseError(f"Project file not found: {db_path}")

    target = db_path.with_name(f"{new_name}{PROJECT_FILE_EXTENSION}")
    if target != db_path and target.exists():
        raise DatabaseError(
            f"A project named '{new_name}' already exists in that folder.",
            "Choose a different name.")

    # Update the name inside the file first: if the rename then fails, the
    # project is still coherent, just at its old filename.
    try:
        conn = open_project_db(db_path)
        try:
            repo = ProjectRepository(conn)
            project = repo.load()
            if project is not None:
                project.name = new_name
                repo.save(project)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise DatabaseError(f"Could not update the project: {exc}")

    if target != db_path:
        try:
            db_path.rename(target)
        except OSError as exc:
            raise DatabaseError(
                f"Could not rename the project file: {exc}",
                "It may be open in another program.")
    log.info("Renamed project %s -> %s", db_path.name, target.name)
    return target


def edit_clip_metadata(db_path: Path, clip_id: str, *, clip_title: str,
                       tags: list[str], notes: str,
                       details: dict[str, str]) -> None:
    """Update one clip's metadata in a CLOSED project file.

    Used by Library Search so clips can be fixed up without opening the
    whole project. Times, overlays and export state are untouched. Callers
    must route through the live session instead when the project is open.
    """
    session = ProjectSession.open(db_path)
    try:
        clip = session.get_clip(clip_id)
        if clip is None:
            raise DatabaseError(
                "That clip no longer exists in the project.",
                "Rebuild the library index to clear stale results.")
        clip.clip_title = clip_title.strip()
        clip.tags = [t.strip() for t in tags if t.strip()]
        clip.notes = notes
        edited = detail_service.merge_editor_details(clip.details, details)
        clip.tags = detail_service.sync_detail_tags(clip.tags, clip.details, edited)
        clip.details = edited
        clip.touch()
        session.save()
    finally:
        session.conn.close()


def duplicate_project(db_path: Path) -> Path:
    """Copy a closed project to "<name> copy" and return the new path.

    The copy gets its stored name updated so the two projects are
    distinguishable everywhere, not just by filename.
    """
    if not db_path.is_file():
        raise DatabaseError(f"Project file not found: {db_path}")
    base = f"{db_path.stem} copy"
    candidate = base
    suffix = 2
    while (db_path.with_name(f"{candidate}{PROJECT_FILE_EXTENSION}")).exists():
        candidate = f"{base} {suffix}"
        suffix += 1
    target = db_path.with_name(f"{candidate}{PROJECT_FILE_EXTENSION}")
    try:
        shutil.copy2(db_path, target)
    except OSError as exc:
        raise DatabaseError(f"Could not copy the project: {exc}")
    try:
        conn = open_project_db(target)
        try:
            repo = ProjectRepository(conn)
            project = repo.load()
            if project is not None:
                project.name = candidate
                repo.save(project)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        target.unlink(missing_ok=True)
        raise DatabaseError(f"Could not initialise the copy: {exc}")
    log.info("Duplicated project %s -> %s", db_path.name, target.name)
    return target


def delete_project(db_path: Path) -> None:
    """Delete a project file. Source video and exports are never touched."""
    if not db_path.is_file():
        raise DatabaseError(f"Project file not found: {db_path}")
    try:
        db_path.unlink()
    except OSError as exc:
        raise DatabaseError(
            f"Could not delete the project: {exc}",
            "It may be open in another program.")
    # SQLite side files, if the DB was left in WAL mode.
    for suffix in ("-wal", "-shm", "-journal"):
        side = db_path.with_name(db_path.name + suffix)
        try:
            side.unlink(missing_ok=True)
        except OSError:
            pass
    log.info("Deleted project %s", db_path.name)


class ProjectSession:
    """The open project: project row, clip list, undo/redo, autosave target."""

    def __init__(
            self, conn: sqlite3.Connection, project: Project, db_path: Path,
            *, read_only: bool = False) -> None:
        self.conn = conn
        self.project = project
        self.db_path = db_path
        self.read_only = bool(read_only)
        self.project_repo = ProjectRepository(conn)
        self.clip_repo = ClipRepository(conn)
        self.autodetect_repo = AutodetectRepository(conn)
        self.clips: list[Clip] = self.clip_repo.list_for_project(project.id)
        self._undo_stack: list[_Snapshot] = []
        self._redo_stack: list[_Snapshot] = []
        self._last_detection_state = self._detection_state(self.clips)
        self._capture_description = ""
        self._pending_autodetect_session: tuple[
            dict[str, Any], list[dict[str, Any]]
        ] | None = None
        self._pending_autodetect_recoveries: list[dict[str, Any]] = []
        self._pending_coverage_review_mutations: list[dict[str, Any]] = []
        self._last_detection_admission: dict[str, Any] = {
            "session_id": "",
            "candidate_count": 0,
            "added_clip_ids": [],
            "reused_clip_ids": [],
        }
        self.dirty = False

    # ---------- lifecycle ----------

    @classmethod
    def create(cls, name: str, folder: Path, output_folder: Path) -> "ProjectSession":
        db_path = folder / f"{name}{PROJECT_FILE_EXTENSION}"
        if db_path.exists():
            raise DatabaseError(
                f"A project file already exists at {db_path}.",
                "Choose a different project name or folder.",
            )
        conn = open_project_db(db_path)
        project = Project(name=name, output_folder=str(output_folder), db_path=str(db_path))
        ProjectRepository(conn).save(project)
        log.info("Created project '%s' at %s", name, db_path)
        return cls(conn, project, db_path)

    @classmethod
    def open(cls, db_path: Path) -> "ProjectSession":
        if not db_path.is_file():
            raise DatabaseError(
                f"Project file not found: {db_path}",
                "It may have been moved or deleted. Remove it from Recent Projects.",
            )
        try:
            conn = open_project_db(db_path)
            project = ProjectRepository(conn).load()
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(
                f"Could not open project file: {exc}",
                "The file may be corrupt. A backup may exist in the same folder.",
            )
        if project is None:
            raise DatabaseError("The project file contains no project data.")
        project.db_path = str(db_path)
        log.info("Opened project '%s' from %s", project.name, db_path)
        return cls(conn, project, db_path)

    @classmethod
    def open_read_only(cls, db_path: Path) -> "ProjectSession":
        """Open a project for research playback without permitting writes."""
        if not db_path.is_file():
            raise DatabaseError(
                f"Project file not found: {db_path}",
                "It may have been moved or deleted.",
            )
        try:
            uri = f"{db_path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA query_only = ON")
            project = ProjectRepository(conn).load()
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(
                f"Could not open project file read-only: {exc}",
                "The file may be corrupt or locked.",
            )
        if project is None:
            conn.close()
            raise DatabaseError("The project file contains no project data.")
        project.db_path = str(db_path)
        log.info("Opened project read-only '%s' from %s", project.name, db_path)
        return cls(conn, project, db_path, read_only=True)

    def close(self) -> None:
        if self.read_only:
            self.conn.close()
            return
        try:
            self.save()
        finally:
            self.conn.close()

    def save(self) -> None:
        if self.read_only:
            raise DatabaseError(
                "This project is open in read-only research mode.",
                "Return to the normal TapeSift shortcut to edit it.",
            )
        self._write()
        self.dirty = False
        log.debug("Saved project '%s' (%d clips)", self.project.name, len(self.clips))

    def _write(self) -> None:
        """Atomically persist project, clips, provenance, and correction event."""
        before = self._last_detection_state
        action = self._capture_action(self._capture_description)
        if action not in {"undo", "redo"}:
            self._invalidate_changed_detection_reviews(before)
        after = self._detection_state(self.clips)
        changed_sessions = self._changed_detection_sessions(before, after)
        occurred_at = utc_now()
        try:
            self.project_repo.save(self.project, commit=False)
            # Replace-all keeps ordering and deletions consistent. Capture
            # tables never foreign-key clip ids, so tombstones survive this.
            self.conn.execute(
                "DELETE FROM clips WHERE project_id=?", (self.project.id,))
            for clip in self.clips:
                clip.project_id = self.project.id
            self.clip_repo.save_many(self.clips, commit=False)

            if self._pending_autodetect_session is not None:
                session, candidates = self._pending_autodetect_session
                self.autodetect_repo.create_session(
                    session, candidates, commit=False)

            for recovery in self._pending_autodetect_recoveries:
                self.autodetect_repo.create_recovery(
                    recovery, commit=False)

            for mutation in self._pending_coverage_review_mutations:
                if mutation["action"] == "upsert":
                    self.autodetect_repo.upsert_coverage_review(
                        mutation["review"], commit=False)
                elif mutation["action"] == "delete":
                    self.autodetect_repo.delete_coverage_review(
                        mutation["session_id"],
                        mutation["segment_index"],
                        commit=False,
                    )

            for session_id in changed_sessions:
                self.autodetect_repo.record_event(
                    session_id,
                    action=action,
                    description=self._capture_description or "save",
                    before=self._state_for_session(before, session_id),
                    after=self._state_for_session(after, session_id),
                    created_at=occurred_at,
                    commit=False,
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self._last_detection_state = after
        self._capture_description = ""
        self._pending_autodetect_session = None
        self._pending_autodetect_recoveries = []
        self._pending_coverage_review_mutations = []

    def commit(self) -> None:
        """Persist immediately (project + clips).

        Every explicit user action that mutates the clip list calls this so
        an edit survives a crash, a force-close, or a forgotten save. Undo
        snapshots are still held in memory; only the *disk* write is eager.
        Called from add/split/reorder/delete/duplicate and clip edits.

        Reindexes first so the persisted order_index matches the current
        in-memory clip order (sort/split/reorder change order without
        touching order_index).
        """
        self._reindex()
        self._write()
        self.dirty = False

    # ---------- undo/redo ----------

    def checkpoint(self, description: str, *, include_naming_template: bool = False,
                   include_logging_defaults: bool = False) -> None:
        """Record current clip state before a mutation."""
        self._undo_stack.append(_Snapshot(copy.deepcopy(self.clips), description,
            self.project.naming_template if include_naming_template else None,
            copy.deepcopy(self.project.logging_defaults) if include_logging_defaults else None))
        del self._undo_stack[:-MAX_UNDO_STEPS]
        self._redo_stack.clear()
        self._describe_detection_change(description)
        self.dirty = True

    def _capture_checkpoint_rollback_state(self) -> dict[str, Any]:
        """Snapshot mutable memory before a checkpointed research decision."""
        return {
            "clips": copy.deepcopy(self.clips),
            "undo_stack": copy.deepcopy(self._undo_stack),
            "redo_stack": copy.deepcopy(self._redo_stack),
            "dirty": self.dirty,
            "capture_description": self._capture_description,
            "pending_recoveries": copy.deepcopy(
                self._pending_autodetect_recoveries),
            "pending_coverage_reviews": copy.deepcopy(
                self._pending_coverage_review_mutations),
            "project_updated_at": self.project.updated_at,
        }

    def _restore_checkpoint_rollback_state(
        self,
        state: dict[str, Any],
    ) -> None:
        self.clips = state["clips"]
        self._undo_stack = state["undo_stack"]
        self._redo_stack = state["redo_stack"]
        self.dirty = state["dirty"]
        self._capture_description = state["capture_description"]
        self._pending_autodetect_recoveries = state["pending_recoveries"]
        self._pending_coverage_review_mutations = \
            state["pending_coverage_reviews"]
        self.project.updated_at = state["project_updated_at"]

    def _commit_after(self) -> None:
        """Snapshot already taken; persist the pending mutation to disk."""
        self.commit()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> str:
        if not self._undo_stack:
            return ""
        snap = self._undo_stack.pop()
        self._redo_stack.append(_Snapshot(copy.deepcopy(self.clips), snap.description,
            self.project.naming_template if snap.naming_template is not None else None,
            copy.deepcopy(self.project.logging_defaults) if snap.logging_defaults is not None else None))
        if snap.logging_defaults is not None:
            self.project.logging_defaults = copy.deepcopy(snap.logging_defaults)
        if snap.naming_template is not None:
            self.project.naming_template = snap.naming_template
        self._describe_detection_change(f"undo {snap.description}")
        self.clips = snap.clips
        self.dirty = True
        self.commit()
        return snap.description

    def redo(self) -> str:
        if not self._redo_stack:
            return ""
        snap = self._redo_stack.pop()
        self._undo_stack.append(_Snapshot(copy.deepcopy(self.clips), snap.description,
            self.project.naming_template if snap.naming_template is not None else None,
            copy.deepcopy(self.project.logging_defaults) if snap.logging_defaults is not None else None))
        if snap.logging_defaults is not None:
            self.project.logging_defaults = copy.deepcopy(snap.logging_defaults)
        if snap.naming_template is not None:
            self.project.naming_template = snap.naming_template
        self._describe_detection_change(f"redo {snap.description}")
        self.clips = snap.clips
        self.dirty = True
        self.commit()
        return snap.description

    # ---------- clip operations ----------

    def next_clip_number(self) -> int:
        return max((c.clip_number for c in self.clips), default=0) + 1

    def add_clip(self, clip: Clip, record_undo: bool = True,
                 chronological: bool = True) -> Clip:
        """Add a clip, by default slotting it in by start time.

        A clip made at 12:43 belongs between the 12:30 and 13:10 clips no
        matter when it was created, so the list keeps mirroring the game.
        """
        if record_undo:
            self.checkpoint("add clip")
        clip.project_id = self.project.id
        if not clip.clip_number:
            clip.clip_number = self.next_clip_number()
        if chronological:
            position = len(self.clips)
            for index, existing in enumerate(self.clips):
                if existing.start_ms > clip.start_ms:
                    position = index
                    break
            self.clips.insert(position, clip)
            self._reindex()
        else:
            clip.order_index = len(self.clips)
            self.clips.append(clip)
        self._commit_after()
        return clip

    def sort_clips_by_time(self, renumber: bool = False, *, number_prefix: bool = False) -> None:
        """Put the whole list back into film order.

        Chronological insertion only governs newly added clips, so a list
        built before that (or reordered by hand) can drift out of sequence.
        Renumbering is optional because clip numbers feed export filenames.
        """
        self.checkpoint("number clips in film order" if number_prefix else "sort clips by time",
                        include_naming_template=number_prefix)
        if number_prefix:
            # Numbering belongs to the template, never the user's editable name.
            # Remove an existing number token so repeated clicks cannot stack it.
            base = (self.project.naming_template or "{clip_name}").replace(
                "{clip_number}", "").strip(" _-.") or "{clip_name}"
            self.project.naming_template = "{clip_number}_" + base
        self.clips.sort(key=lambda c: c.start_ms)
        if renumber or number_prefix:
            for number, clip in enumerate(self.clips, start=1):
                if clip.clip_number != number:
                    clip.clip_number = number
                    clip.touch()
        self._commit_after()

    def split_clip(self, clip_id: str, at_ms: int) -> Clip | None:
        """Split a clip in two at `at_ms`, keeping both in place.

        The tail becomes the next numbered play in the list. It carries the
        quarter forward, but starts otherwise unlogged so each separated play
        can be described independently.
        """
        source = self.get_clip(clip_id)
        if source is None:
            return None
        if not (source.start_ms < at_ms < source.end_ms):
            raise DatabaseError(
                "The playhead must sit inside the clip to split it.",
                "Move the playhead between the clip's in and out points.")
        self.checkpoint("split clip")
        tail = source.copy_as_new()
        source.analysis.pop(PREDICTION_KEY, None)
        tail.analysis.pop(PREDICTION_KEY, None)
        if tail.detection_lineage:
            tail.detection_lineage["parent_clip_id"] = source.id
            source_derivation = str(
                source.detection_lineage.get("derivation", "detected"))
            tail.detection_lineage["derivation"] = (
                "duplicate_split"
                if source_derivation.startswith("duplicate")
                else "split"
            )
            tail.detection_lineage["reviewed_at"] = ""
            tail.detection_lineage["false_positive_confirmed_at"] = ""
            tail.detection_lineage["recovery_withdrawn_at"] = ""
        tail.project_id = self.project.id
        tail_number = source.clip_number + 1
        for existing in self.clips:
            if existing is not source and existing.clip_number >= tail_number:
                existing.clip_number += 1
                existing.touch()
        tail.clip_number = tail_number
        tail.start_ms = at_ms
        tail.end_ms = source.end_ms
        tail.central_timestamp_ms = None
        tail.clip_title = ""
        tail.output_filename_base = ""
        tail.label = ""
        tail.tags = []
        tail.notes = ""
        tail.source_photo = {}
        tail.source_photo_png = b""
        quarter = source.details.get("quarter", "")
        tail.details = {"quarter": quarter} if quarter else {}
        source.end_ms = at_ms
        source.touch()
        self.clips.insert(self.clips.index(source) + 1, tail)
        self._reindex()
        self._commit_after()
        return tail

    def merge_clips(self, clip_ids: list[str]) -> Clip | None:
        """Fuse several clips into the span they cover, starting unlogged.

        The inverse of split_clip: the detector cuts one play in two when the
        broadcast switches camera angle mid-play, and this puts it back. The
        survivor keeps the earliest clip's identity and number, and spans from
        the earliest start to the latest end - including any gap between them,
        because the footage in that gap is the same play.

        Metadata is deliberately not merged. Two descriptions of what turned
        out to be one play cannot be combined without guessing which is right,
        so the result starts blank and gets described once.
        """
        wanted = list(dict.fromkeys(clip_ids))
        targets = [clip for clip in self.clips if clip.id in set(wanted)]
        if len(targets) < 2:
            return None
        self.checkpoint(f"merge {len(targets)} clips")
        targets.sort(key=lambda clip: (clip.start_ms, clip.end_ms))
        survivor = targets[0]
        absorbed = targets[1:]

        survivor.start_ms = min(clip.start_ms for clip in targets)
        survivor.end_ms = max(clip.end_ms for clip in targets)
        survivor.central_timestamp_ms = None
        survivor.clip_title = ""
        survivor.output_filename_base = ""
        survivor.label = ""
        survivor.tags = []
        survivor.notes = ""
        survivor.details = {}
        survivor.source_photo = {}
        survivor.source_photo_png = b""
        survivor.analysis.pop(PREDICTION_KEY, None)
        if survivor.detection_lineage:
            survivor.detection_lineage["derivation"] = "merge"
            survivor.detection_lineage["merged_clip_ids"] = [
                clip.id for clip in absorbed]
            survivor.detection_lineage["reviewed_at"] = ""
            survivor.detection_lineage["false_positive_confirmed_at"] = ""
        survivor.touch()

        absorbed_ids = {clip.id for clip in absorbed}
        self.clips = [
            clip for clip in self.clips if clip.id not in absorbed_ids]
        self._reindex()
        self._commit_after()
        return survivor

    def set_source_photo(self, clip_id: str, photo: dict, png: bytes) -> None:
        """Save or remove evidence as one reversible, atomic clip action."""
        from tapesift.services.source_photo import validate_photo

        if self.read_only:
            raise DatabaseError("This project is open read-only.")
        clip = self.get_clip(clip_id)
        if clip is None:
            raise DatabaseError("The selected clip is no longer in this project.")
        error = validate_photo(photo, png, clip_id) if photo or png else ""
        if error:
            raise DatabaseError(error)
        if clip.source_photo == photo and clip.source_photo_png == png:
            return
        state = self._capture_checkpoint_rollback_state()
        try:
            self.checkpoint("source photo")
            clip.source_photo = copy.deepcopy(photo)
            clip.source_photo_png = png
            self.commit()
        except Exception:
            self._restore_checkpoint_rollback_state(state)
            raise

    def correct_snap_point(self, clip_id: str, source_ms: int) -> None:
        """Persist the displayed snap frame as one undoable user correction."""
        if self.read_only:
            raise DatabaseError("This project is open read-only.")
        clip = self.get_clip(clip_id)
        if clip is None:
            raise DatabaseError("The selected clip is no longer in this project.")
        if type(source_ms) is not int or not clip.start_ms <= source_ms < clip.end_ms:
            raise DatabaseError("Choose a settled frame within the selected play.")
        values = {"timing_snap_ms": str(source_ms), "timing_snap_confirmed": "1"}
        if all(clip.details.get(key) == value for key, value in values.items()):
            return
        state = self._capture_checkpoint_rollback_state()
        try:
            self.checkpoint("correct snap point")
            clip.details = dict(clip.details, **values)
            clip.touch()
            self.commit()
        except Exception:
            self._restore_checkpoint_rollback_state(state)
            raise

    def apply_details_to_clips(
            self, clip_ids: list[str], values: dict[str, str], *,
            preserve_distance: bool = False) -> int:
        """Set the same fields on several plays, under one undo step.

        Marking a dozen plays as Q3 was a dozen separate edits and a dozen
        undo steps to take back. One checkpoint covers the lot, so undo
        reverses the intent rather than the last keystroke of it.

        An empty value clears the field, which is how a mistaken bulk edit
        gets taken back without undo.
        """
        wanted = set(clip_ids)
        targets = [clip for clip in self.clips if clip.id in wanted]
        if not targets or not values:
            return 0
        self.checkpoint(f"set {len(values)} fields on {len(targets)} clips")
        for clip in targets:
            details = dict(clip.details)
            for field, value in values.items():
                text = str(value or "").strip()
                if preserve_distance and field == "down_distance":
                    from tapesift.services.football_context import parse_down_distance, down_distance
                    down, _ = parse_down_distance(text)
                    _, distance = parse_down_distance(details.get(field, ""))
                    text = down_distance(down, distance)
                if text:
                    details[field] = text
                else:
                    details.pop(field, None)
            clip.tags = detail_service.sync_detail_tags(clip.tags, clip.details, details)
            clip.details = details
            clip.touch()
        self._commit_after()
        return len(targets)

    def add_clips(self, clips: list[Clip]) -> None:
        """Bulk add (detection, CSV, bulk paste) - appended, then sorted once."""
        self.checkpoint(f"add {len(clips)} clips")
        for clip in clips:
            self.add_clip(clip, record_undo=False, chronological=False)
        self.clips.sort(key=lambda c: c.start_ms)
        self._reindex()
        self._commit_after()

    def remove_clips(self, clip_ids: list[str]) -> None:
        self.checkpoint("delete clips")
        ids = set(clip_ids)
        self.clips = [c for c in self.clips if c.id not in ids]
        self._reindex()
        self._commit_after()

    def duplicate_clip(self, clip_id: str) -> Clip | None:
        """Duplicate a clip as a new version (same range, independent metadata).

        Versions are named "Title v2", "Title v3", … so the same moment can be
        exported several times with different metadata.
        """
        source = self.get_clip(clip_id)
        if source is None:
            return None
        self.checkpoint("duplicate clip")
        dup = source.copy_as_new()
        if dup.detection_lineage:
            dup.detection_lineage["parent_clip_id"] = source.id
            dup.detection_lineage["derivation"] = "duplicate"
        dup.project_id = self.project.id
        dup.clip_number = self.next_clip_number()
        if dup.clip_title:
            dup.clip_title = self._next_version_title(dup.clip_title)
            # Re-derive the filename from the versioned title so each export
            # is distinct; an explicit custom base would otherwise collide.
            dup.output_filename_base = ""
        insert_at = self.clips.index(source) + 1
        self.clips.insert(insert_at, dup)
        self._reindex()
        self._commit_after()
        return dup

    _VERSION_RE = re.compile(r"^(?P<base>.*?)\s+v(?P<n>\d+)$")

    def _next_version_title(self, title: str) -> str:
        m = self._VERSION_RE.match(title)
        base = m.group("base") if m else title
        highest = 1
        for clip in self.clips:
            other = self._VERSION_RE.match(clip.clip_title)
            if other and other.group("base") == base:
                highest = max(highest, int(other.group("n")))
            elif clip.clip_title == base:
                highest = max(highest, 1)
        return f"{base} v{highest + 1}"

    def move_clip(self, from_index: int, to_index: int) -> None:
        if from_index == to_index:
            return
        if not (0 <= from_index < len(self.clips)) or not (0 <= to_index < len(self.clips)):
            return
        self.checkpoint("reorder clips")
        clip = self.clips.pop(from_index)
        self.clips.insert(to_index, clip)
        self._reindex()
        self._commit_after()

    def get_clip(self, clip_id: str) -> Clip | None:
        return next((c for c in self.clips if c.id == clip_id), None)

    def detector_angle_starts(self, clip_id: str) -> tuple[int, ...]:
        """CSE camera-view starts belonging to one current clip.

        The values come from immutable detection candidates rather than from
        editable metadata. Manual clips simply return their own start so the
        snap estimator can still analyze them as a single view.
        """
        clip = self.get_clip(clip_id)
        if clip is None:
            return ()
        lineage = clip.detection_lineage
        session_id = str(lineage.get("session_id", ""))
        roots = lineage.get("candidate_ids", [])
        if isinstance(roots, str):
            roots = [roots]
        root_ids = {str(value) for value in roots}
        starts = {int(clip.start_ms)}
        if session_id and root_ids:
            candidates = [
                candidate
                for candidate in self.autodetect_repo.list_candidates(
                    session_id)
                if str(candidate.get("id", "")) in root_ids
            ]
            for candidate in sorted(
                    candidates,
                    key=lambda item: int(item.get("detector_start_ms", 0))):
                for value in candidate.get("angle_starts_ms", []):
                    point = int(value)
                    if clip.start_ms <= point < clip.end_ms:
                        starts.add(point)
        return tuple(sorted(starts))

    def cache_snap_prediction(
            self, clip_id: str, prediction: dict[str, Any]) -> Clip | None:
        """Persist replaceable machine analysis without creating an undo edit."""
        clip = self.get_clip(clip_id)
        if clip is None:
            return None
        clip.analysis = copy.deepcopy(clip.analysis)
        clip.analysis[PREDICTION_KEY] = copy.deepcopy(prediction)
        clip.touch()
        self.dirty = True
        self.save()
        return clip

    def trim_clip_boundary(
            self, clip_id: str, edge: str, position_ms: int,
            minimum_duration_ms: int = MIN_CLIP_DURATION_MS) -> Clip | None:
        """Move one clip edge as one durable, undoable operation."""
        clip = self.get_clip(clip_id)
        if clip is None:
            return None
        if edge not in {"start", "end"}:
            raise ValueError(f"Unknown clip edge: {edge}")

        minimum = max(1, int(minimum_duration_ms))
        position = max(0, int(position_ms))
        if edge == "start":
            position = min(position, max(0, clip.end_ms - minimum))
            if position == clip.start_ms:
                return clip
        else:
            position = max(position, clip.start_ms + minimum)
            if self.project.source_duration_ms > 0:
                position = min(position, self.project.source_duration_ms)
            if position == clip.end_ms:
                return clip

        self.checkpoint("trim clip")
        clip.analysis.pop(PREDICTION_KEY, None)
        if edge == "start":
            clip.start_ms = position
        else:
            clip.end_ms = position
        clip.touch()
        self._commit_after()
        return clip

    def set_clip_enabled(self, clip_id: str, enabled: bool) -> Clip | None:
        """Include or exclude one clip as a durable correction."""
        clip = self.get_clip(clip_id)
        if clip is None or clip.enabled == bool(enabled):
            return clip
        self.checkpoint("restore clip" if enabled else "exclude clip")
        clip.enabled = bool(enabled)
        if clip.detection_lineage:
            # Export inclusion is an editorial control, not a research
            # disposition. Only confirm_detection_false_positive() may
            # certify an excluded detector proposal as a false positive.
            clip.detection_lineage["reviewed_at"] = ""
            clip.detection_lineage["false_positive_confirmed_at"] = ""
            clip.detection_lineage["recovery_withdrawn_at"] = ""
        clip.touch()
        self._commit_after()
        return clip

    def store_clip_analysis(self, clip_id: str, analysis_json: str) -> Clip | None:
        """Replace one clip's analysis payload.

        Analysis is machine output - a First Read suggestion, a predicted
        snap - and is deliberately a different field from ``details``, which
        holds what the analyst decided. Writing here can never change a
        label, and no undo checkpoint is taken: a suggestion arriving in the
        background is not an edit the analyst should have to undo.
        """
        clip = self.get_clip(clip_id)
        if clip is None:
            return None
        try:
            payload = json.loads(analysis_json) if analysis_json else {}
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        clip.analysis = payload
        clip.touch()
        self.dirty = True
        return clip

    def mark_detection_reviewed(self, clip_ids: list[str]) -> int:
        """Explicitly confirm selected detected clips after human review.

        This action is the only trustworthy unchanged-acceptance signal.
        Merely creating detector clips, saving metadata, or exporting them
        never certifies detector accuracy.
        """
        active_batch = self._assert_active_batch_source()
        owned_candidate_ids = (
            self._batch_owned_candidate_ids(active_batch)
            if active_batch is not None else set()
        )

        def belongs_to_active_batch(clip: Clip) -> bool:
            if active_batch is None:
                return False
            lineage = clip.detection_lineage
            return (
                lineage.get("batch_id") == active_batch["id"]
                or bool(
                    self._lineage_candidate_ids(lineage)
                    & owned_candidate_ids
                )
            )

        selected = set(clip_ids)
        targets = [
            clip for clip in self.clips
            if clip.id in selected
            and clip.detection_lineage
            and clip.enabled
            and (
                not clip.detection_lineage.get("reviewed_at")
                or (
                    belongs_to_active_batch(clip)
                    and clip.detection_lineage.get("review_batch_id")
                    != active_batch["id"]
                )
            )
        ]
        if not targets:
            return 0
        rollback_state = self._capture_checkpoint_rollback_state()
        self.checkpoint(
            f"mark {len(targets)} detected clip"
            f"{'s' if len(targets) != 1 else ''} reviewed")
        reviewed_at = utc_now()
        for clip in targets:
            clip.detection_lineage["reviewed_at"] = reviewed_at
            if belongs_to_active_batch(clip):
                clip.detection_lineage["review_batch_id"] = active_batch["id"]
            clip.tags = [tag for tag in clip.tags if tag != REVIEW_TAG]
            marker = f"{REVIEW_PREFIX} "
            if clip.clip_title.startswith(marker):
                clip.clip_title = clip.clip_title[len(marker):]
            clip.touch()
        try:
            self._commit_after()
        except Exception:
            self._restore_checkpoint_rollback_state(rollback_state)
            raise
        return len(targets)

    def start_autodetect_review_batch(
        self,
        start_ms: int,
        end_ms: int,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Freeze one contiguous, short source range for honest local scoring."""
        start = max(0, int(start_ms))
        end = int(end_ms)
        if end <= start:
            raise DatabaseError(
                "The autodetect test range must end after it starts.",
                "Set an In point, then set a later Out point.",
            )
        if self.project.source_duration_ms > 0:
            end = min(end, self.project.source_duration_ms)
        if end <= start:
            raise DatabaseError(
                "The autodetect test range falls outside the source film.")
        if self.autodetect_repo.get_active_review_batch() is not None:
            raise DatabaseError(
                "An autodetect test batch is already active.",
                "Finish the active batch before starting another one.",
            )

        sessions = self.autodetect_repo.list_sessions(self.project.id)
        if not sessions:
            raise DatabaseError(
                "Run Detect Plays before starting a test batch.")
        if session_id is None:
            session = sessions[-1]
        else:
            session = next(
                (item for item in sessions if item["id"] == session_id),
                None,
            )
            if session is None:
                raise DatabaseError(
                    f"Autodetect session not found: {session_id}")
        self._assert_capture_matches_current_source(session)
        if self._foreign_autodetect_truth_in_range(
            session_id=session["id"],
            start_ms=start,
            end_ms=end,
        ):
            raise DatabaseError(
                "That range still contains clips linked to an older "
                "autodetect run.",
                "Use a clean, non-overlapping range or a fresh project for "
                "this detector iteration so known plays cannot be omitted.",
            )
        selected_film_id = str(
            session.get("source", {}).get("film_id", ""))
        for previous_session in sessions:
            previous_film_id = str(
                previous_session.get("source", {}).get("film_id", ""))
            same_film = (
                bool(selected_film_id)
                and selected_film_id == previous_film_id
            ) or self._same_source_identity(
                session.get("source", {}),
                previous_session.get("source", {}),
            )
            if not same_film:
                continue
            for previous_batch in \
                    self.autodetect_repo.list_review_batches(
                        previous_session["id"]):
                if (
                    previous_batch["status"] == "completed"
                    and int(previous_batch["start_ms"]) < end
                    and int(previous_batch["end_ms"]) > start
                ):
                    raise DatabaseError(
                        "That source range overlaps a completed autodetect "
                        "test batch.",
                        "Choose a fresh, non-overlapping range so an earlier "
                        "review cannot hide known misses or bias this score.",
                    )

        candidates = self.autodetect_repo.list_candidates(session["id"])
        overlapping = [
            item for item in candidates
            if int(item["created_start_ms"]) < end
            and int(item["created_end_ms"]) > start
        ]
        crossing = [
            item for item in overlapping
            if not (
                int(item["created_start_ms"]) >= start
                and int(item["created_end_ms"]) <= end
            )
        ]
        if crossing:
            raise DatabaseError(
                "The test range cuts through an autodetect candidate.",
                "Move the In/Out marks into gaps between clips so every "
                "candidate is either fully inside or outside the batch.",
            )
        if not overlapping:
            raise DatabaseError(
                "No autodetect candidates fall inside that range.",
                "Choose a section containing roughly 20–30 detected plays.",
            )
        owned_ids = {str(item["id"]) for item in overlapping}
        intruding_current_truth: list[Clip] = []
        escaping_current_truth: list[Clip] = []
        for clip in self.clips:
            lineage = clip.detection_lineage
            if (
                not clip.enabled
                or lineage.get("session_id") != session["id"]
                or str(
                    lineage.get("derivation", "")
                ).startswith("duplicate")
            ):
                continue
            roots = lineage.get("candidate_ids", [])
            if isinstance(roots, str):
                roots = [roots]
            roots = {str(root) for root in roots}
            if not roots:
                continue
            intersects = clip.start_ms < end and clip.end_ms > start
            if intersects and roots - owned_ids:
                intruding_current_truth.append(clip)
            if roots & owned_ids and (
                clip.start_ms < start or clip.end_ms > end
            ):
                escaping_current_truth.append(clip)
        if intruding_current_truth or escaping_current_truth:
            raise DatabaseError(
                "Current corrected clip boundaries do not match that test "
                "range's immutable detector roots.",
                "Choose different In/Out marks, or undo the relocation before "
                "starting the batch.",
            )

        batch = {
            "id": new_capture_id("batch"),
            "session_id": session["id"],
            "start_ms": start,
            "end_ms": end,
            "status": "active",
            "confirmation": {
                "review_decision_baseline": self._review_decision_baseline(
                    session["id"], owned_ids),
            },
            "started_at": utc_now(),
            "completed_at": "",
        }
        try:
            self.autodetect_repo.create_review_batch(batch)
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            raise DatabaseError(
                "Another autodetect test batch is already active.",
                "Finish or cancel it before starting a new range.",
            ) from exc
        return batch

    def active_autodetect_review_batch(self) -> dict[str, Any] | None:
        return self.autodetect_repo.get_active_review_batch()

    def _foreign_autodetect_truth_in_range(
        self,
        *,
        session_id: str,
        start_ms: int,
        end_ms: int,
    ) -> list[Clip]:
        return [
            clip for clip in self.clips
            if clip.enabled
            and clip.start_ms < int(end_ms)
            and clip.end_ms > int(start_ms)
            and clip.detection_lineage.get("session_id")
            and clip.detection_lineage.get("session_id") != session_id
            and not str(
                clip.detection_lineage.get("derivation", "")
            ).startswith("duplicate")
        ]

    @staticmethod
    def _lineage_candidate_ids(lineage: dict[str, Any]) -> set[str]:
        roots = lineage.get("candidate_ids", [])
        if isinstance(roots, str):
            roots = [roots]
        return {str(root) for root in roots}

    def _batch_owned_candidate_ids(
        self,
        batch: dict[str, Any],
    ) -> set[str]:
        return {
            str(candidate["id"])
            for candidate in self.autodetect_repo.list_candidates(
                batch["session_id"])
            if int(candidate["created_start_ms"]) >= int(batch["start_ms"])
            and int(candidate["created_end_ms"]) <= int(batch["end_ms"])
        }

    def _review_decision_baseline(
        self,
        session_id: str,
        candidate_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Record pre-batch certifications without allowing them to score."""
        return sorted(
            [
                {
                    "clip_id": clip.id,
                    "candidate_ids": sorted(
                        self._lineage_candidate_ids(
                            clip.detection_lineage) & candidate_ids),
                    "reviewed_at": str(
                        clip.detection_lineage.get("reviewed_at", "")),
                    "false_positive_confirmed_at": str(
                        clip.detection_lineage.get(
                            "false_positive_confirmed_at", "")),
                }
                for clip in self.clips
                if clip.detection_lineage.get("session_id") == session_id
                and self._lineage_candidate_ids(clip.detection_lineage)
                & candidate_ids
                and not str(
                    clip.detection_lineage.get("derivation", "")
                ).startswith("duplicate")
            ],
            key=lambda item: item["clip_id"],
        )

    def validate_active_autodetect_batch_source(self) -> dict[str, Any]:
        """Return the active batch only when its captured film is still loaded."""
        batch = self.autodetect_repo.get_active_review_batch()
        if batch is None:
            raise DatabaseError(
                "There is no active autodetect test batch.")
        captured_session = self.autodetect_repo.get_session(
            batch["session_id"])
        if captured_session is None:
            raise DatabaseError("The autodetect capture no longer exists.")
        self._assert_capture_matches_current_source(captured_session)
        return batch

    def mark_missed_detection(
        self,
        clip_ids: list[str],
        *,
        batch_id: str | None = None,
        creation_method: str = "existing_clip_context_action",
    ) -> int:
        """Explicitly attach ordinary clips as plays missed in an active batch."""
        batch = (
            self.autodetect_repo.get_review_batch(batch_id)
            if batch_id else self.autodetect_repo.get_active_review_batch()
        )
        if batch is None or batch["status"] != "active":
            raise DatabaseError(
                "Start an autodetect test batch before marking missed plays.",
                "Set In/Out around a short reviewed section, then use "
                "Playback > Start Autodetect Test Batch.",
            )
        captured_session = self.autodetect_repo.get_session(
            batch["session_id"])
        if captured_session is None:
            raise DatabaseError("The autodetect capture no longer exists.")
        self._assert_capture_matches_current_source(captured_session)
        selected = set(clip_ids)
        targets = [
            clip for clip in self.clips
            if clip.id in selected and not clip.detection_lineage
        ]
        if not targets:
            return 0
        outside = [
            clip for clip in targets
            if clip.start_ms < batch["start_ms"]
            or clip.end_ms > batch["end_ms"]
        ]
        if outside:
            raise DatabaseError(
                "A selected clip falls outside the active test batch.",
                "Only mark manually recovered plays wholly inside the batch.",
            )

        before_clips = copy.deepcopy(self.clips)
        before_undo = copy.deepcopy(self._undo_stack)
        before_redo = copy.deepcopy(self._redo_stack)
        before_dirty = self.dirty
        before_description = self._capture_description
        before_pending = copy.deepcopy(self._pending_autodetect_recoveries)
        before_project_updated_at = self.project.updated_at

        self.checkpoint(
            f"mark {len(targets)} missed autodetect play"
            f"{'s' if len(targets) != 1 else ''}")
        recorded_at = utc_now()
        recoveries: list[dict[str, Any]] = []
        for clip in targets:
            recovery_id = new_capture_id("recovery")
            recovery = {
                "id": recovery_id,
                "session_id": batch["session_id"],
                "batch_id": batch["id"],
                "initial_clip_id": clip.id,
                "created_start_ms": int(clip.start_ms),
                "created_end_ms": int(clip.end_ms),
                "creation_method": creation_method,
                "created_at": recorded_at,
            }
            clip.detection_lineage = {
                "schema_version": "1.1",
                "session_id": batch["session_id"],
                "candidate_ids": [],
                "recovery_id": recovery_id,
                "recovery_kind": "missed_play",
                "batch_id": batch["id"],
                "origin_clip_id": clip.id,
                "derivation": "manual_recovery",
                # The explicit "missed play" action is itself the human
                # confirmation; later geometry edits clear this timestamp.
                "reviewed_at": recorded_at,
                "review_batch_id": batch["id"],
            }
            clip.touch()
            recoveries.append(recovery)
        self._pending_autodetect_recoveries.extend(recoveries)
        try:
            self._commit_after()
        except Exception:
            self.clips = before_clips
            self._undo_stack = before_undo
            self._redo_stack = before_redo
            self.dirty = before_dirty
            self._capture_description = before_description
            self._pending_autodetect_recoveries = before_pending
            self.project.updated_at = before_project_updated_at
            raise
        return len(targets)

    def withdraw_missed_detection(self, clip_ids: list[str]) -> int:
        """Explicitly withdraw mistaken manual-recovery roots.

        Ordinary disable/delete operations are editorial controls and cannot
        erase a measured false negative. This action keeps the correction rows
        present while recording the user's deliberate withdrawal.
        """
        batch = self.autodetect_repo.get_active_review_batch()
        if batch is None:
            raise DatabaseError(
                "There is no active autodetect test batch.")
        captured_session = self.autodetect_repo.get_session(
            batch["session_id"])
        if captured_session is None:
            raise DatabaseError("The autodetect capture no longer exists.")
        self._assert_capture_matches_current_source(captured_session)

        selected = set(clip_ids)
        recovery_ids = {
            str(clip.detection_lineage.get("recovery_id"))
            for clip in self.clips
            if clip.id in selected
            and clip.detection_lineage.get("batch_id") == batch["id"]
            and clip.detection_lineage.get("recovery_id")
            and not str(
                clip.detection_lineage.get("derivation", "")
            ).startswith("duplicate")
        }
        pending_roots = {
            recovery_id for recovery_id in recovery_ids
            if any(
                str(clip.detection_lineage.get("recovery_id"))
                == recovery_id
                and not str(
                    clip.detection_lineage.get("derivation", "")
                ).startswith("duplicate")
                and (
                    clip.enabled
                    or not clip.detection_lineage.get(
                        "recovery_withdrawn_at")
                )
                for clip in self.clips
            )
        }
        if not pending_roots:
            return 0

        rollback_state = self._capture_checkpoint_rollback_state()
        self.checkpoint(
            f"withdraw {len(pending_roots)} missed autodetect play"
            f"{'s' if len(pending_roots) != 1 else ''}")
        withdrawn_at = utc_now()
        for clip in self.clips:
            lineage = clip.detection_lineage
            if (
                str(lineage.get("recovery_id")) in pending_roots
                and not str(
                    lineage.get("derivation", "")
                ).startswith("duplicate")
            ):
                clip.enabled = False
                lineage["reviewed_at"] = withdrawn_at
                lineage["recovery_withdrawn_at"] = withdrawn_at
                clip.touch()
        try:
            self._commit_after()
        except Exception:
            self._restore_checkpoint_rollback_state(rollback_state)
            raise
        return len(pending_roots)

    def confirm_detection_false_positive(self, clip_ids: list[str]) -> int:
        """Explicitly confirm selected detector clips are not real plays."""
        active_batch = self._assert_active_batch_source()
        owned_candidate_ids = (
            self._batch_owned_candidate_ids(active_batch)
            if active_batch is not None else set()
        )

        def belongs_to_active_batch(clip: Clip) -> bool:
            return bool(
                active_batch is not None
                and self._lineage_candidate_ids(clip.detection_lineage)
                & owned_candidate_ids
            )

        selected = set(clip_ids)
        targets = [
            clip for clip in self.clips
            if clip.id in selected
            and clip.detection_lineage.get("candidate_ids")
            and not str(
                clip.detection_lineage.get("derivation", "")
            ).startswith("duplicate")
            and (
                clip.enabled
                or not clip.detection_lineage.get(
                    "false_positive_confirmed_at")
                or (
                    belongs_to_active_batch(clip)
                    and clip.detection_lineage.get("review_batch_id")
                    != active_batch["id"]
                )
            )
        ]
        if not targets:
            return 0
        rollback_state = self._capture_checkpoint_rollback_state()
        self.checkpoint(
            f"confirm {len(targets)} autodetect false positive"
            f"{'s' if len(targets) != 1 else ''}")
        reviewed_at = utc_now()
        for clip in targets:
            clip.enabled = False
            clip.detection_lineage["reviewed_at"] = reviewed_at
            clip.detection_lineage[
                "false_positive_confirmed_at"] = reviewed_at
            if belongs_to_active_batch(clip):
                clip.detection_lineage["review_batch_id"] = active_batch["id"]
            clip.tags = [tag for tag in clip.tags if tag != REVIEW_TAG]
            marker = f"{REVIEW_PREFIX} "
            if clip.clip_title.startswith(marker):
                clip.clip_title = clip.clip_title[len(marker):]
            clip.touch()
        try:
            self._commit_after()
        except Exception:
            self._restore_checkpoint_rollback_state(rollback_state)
            raise
        return len(targets)

    def complete_autodetect_review_batch(
        self,
        *,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Finish an exhaustive short-range review only when it is scoreable."""
        if self.conn.in_transaction:
            raise DatabaseError(
                "The project is already busy with another database operation.",
                "Wait for it to finish, then complete the test batch again.",
            )
        if self.dirty:
            raise DatabaseError(
                "The project has unsaved edits.",
                "Finish or save the current edit, then complete the test batch "
                "again so the frozen score matches what is on screen.",
            )
        try:
            # Freeze the exact evaluated rows under one write reservation.
            # This prevents another app connection from editing clips between
            # score validation and the immutable completion snapshot.
            self.conn.execute("BEGIN IMMEDIATE")
            batch = (
                self.autodetect_repo.get_review_batch(batch_id)
                if batch_id
                else self.autodetect_repo.get_active_review_batch()
            )
            if batch is None or batch["status"] != "active":
                raise DatabaseError(
                    "There is no active autodetect test batch.")
            captured_session = self.autodetect_repo.get_session(
                batch["session_id"])
            if captured_session is None:
                raise DatabaseError(
                    "The autodetect capture no longer exists.")
            self._assert_capture_matches_current_source(captured_session)
            if self._foreign_autodetect_truth_in_range(
                session_id=batch["session_id"],
                start_ms=batch["start_ms"],
                end_ms=batch["end_ms"],
            ):
                raise DatabaseError(
                    "The test range contains truth linked to another "
                    "autodetect run.",
                    "Cancel this batch and use a clean range or fresh project "
                    "for the detector iteration.",
                )

            # Imported lazily to keep persistence independent of export
            # assembly.
            from tapesift.services.autodetect_export_service import (
                build_correction_bundle,
            )
            bundle = build_correction_bundle(
                self.conn, session_selector=batch["session_id"])
            result = next(
                (
                    item for item in bundle.get("review_batches", [])
                    if item["id"] == batch["id"]
                ),
                None,
            )
            if result is None:
                raise DatabaseError(
                    "The active autodetect batch could not be read.")
            pending = result["pending_candidate_count"]
            pending_recoveries = result["pending_recovery_count"]
            if not result["candidate_capture_valid"]:
                raise DatabaseError(
                    "The captured candidates do not match the immutable "
                    "detector result.",
                    "Run Detect Plays again before collecting a score.",
                )
            if result.get("omitted_detector_play_ranges"):
                raise DatabaseError(
                    "Detector play predictions were omitted before this batch.",
                    "Run detection keeping all play candidates, then confirm false positives during batch review.")
            if pending or pending_recoveries:
                raise DatabaseError(
                    "The test batch still has unconfirmed corrections.",
                    f"Review {pending} remaining detector candidate(s) and "
                    f"{pending_recoveries} recovered miss(es), then finish "
                    "again.",
                )
            if result["crossing_candidate_ids"]:
                raise DatabaseError(
                    "A detector candidate crosses the test-batch boundary.",
                    "Restart the batch with In/Out marks in gaps between "
                    "clips.",
                )
            if result["intruding_candidate_truth_ranges"]:
                raise DatabaseError(
                    "A correction from outside the test batch now overlaps "
                    "its source range.",
                    "Undo that relocation or choose a range whose detector "
                    "roots and corrected truth remain inside the same "
                    "boundary.",
                )
            if result["out_of_scope_truth_ranges"]:
                raise DatabaseError(
                    "A corrected play now extends outside the test-batch "
                    "range.",
                    "Move the batch boundary or keep confirmed truth wholly "
                    "inside the reviewed source range.",
                )
            if result["overlapping_truth_pairs"]:
                raise DatabaseError(
                    "Two correction roots appear to describe the same play.",
                    "Remove or withdraw the duplicate manual/detected clip "
                    "before finishing the batch.",
                )
            if result["manual_prediction_overlaps"]:
                raise DatabaseError(
                    "A manually recovered miss overlaps a detector play "
                    "proposal.",
                    "Correct or split the detector clip instead of counting "
                    "the same interval as both a false positive and a missed "
                    "play.",
                )
            if not result["development_score"]["available"]:
                raise DatabaseError(
                    "The test batch contains no detector decisions to score.")

            self.autodetect_repo.complete_review_batch(
                batch["id"],
                confirmation={
                    "complete_source_range_reviewed": True,
                    "all_missed_plays_added": True,
                    "scope": "contiguous_source_range",
                    "frozen_candidates": result["confirmed_candidates"],
                    "frozen_manual_recoveries": result[
                        "confirmed_manual_recoveries"],
                    "frozen_intruding_candidate_truth_ranges": result[
                        "intruding_candidate_truth_ranges"],
                    "frozen_snapshot_sha256": result[
                        "computed_completion_snapshot_sha256"],
                    "review_decision_baseline": batch.get(
                        "confirmation", {}).get(
                            "review_decision_baseline", []),
                },
                commit=False,
            )
            completed = self.autodetect_repo.get_review_batch(batch["id"])
            if completed is None:
                raise DatabaseError(
                    "The completed autodetect batch could not be read.")
            self.conn.commit()
            return completed
        except sqlite3.OperationalError as exc:
            self.conn.rollback()
            if any(
                marker in str(exc).lower()
                for marker in ("locked", "busy")
            ):
                raise DatabaseError(
                    "The project is busy in another TapeSift window.",
                    "Wait for that edit to finish, then complete the test "
                    "batch again.",
                ) from exc
            raise
        except Exception:
            self.conn.rollback()
            raise

    def cancel_autodetect_review_batch(
        self,
        *,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an accidental batch and release its clips for a clean retry."""
        batch = (
            self.autodetect_repo.get_review_batch(batch_id)
            if batch_id else self.autodetect_repo.get_active_review_batch()
        )
        if batch is None or batch["status"] != "active":
            raise DatabaseError("There is no active autodetect test batch.")
        before_clips = copy.deepcopy(self.clips)
        before_undo = copy.deepcopy(self._undo_stack)
        before_redo = copy.deepcopy(self._redo_stack)
        before_dirty = self.dirty
        before_description = self._capture_description
        before_project_updated_at = self.project.updated_at
        self._describe_detection_change("cancel autodetect test batch")
        for clip in self.clips:
            lineage = clip.detection_lineage
            if not lineage:
                continue
            if lineage.get("batch_id") == batch["id"]:
                # The clip remains a normal user clip. Only the cancelled
                # research label is withdrawn.
                clip.detection_lineage = {}
                clip.touch()
            elif lineage.get("review_batch_id") == batch["id"]:
                lineage["reviewed_at"] = ""
                lineage["false_positive_confirmed_at"] = ""
                lineage["recovery_withdrawn_at"] = ""
                lineage["review_batch_id"] = ""
                clip.touch()
        for snapshot in [*self._undo_stack, *self._redo_stack]:
            for clip in snapshot.clips:
                if clip.detection_lineage.get("batch_id") == batch["id"]:
                    clip.detection_lineage = {}
                elif clip.detection_lineage.get(
                        "review_batch_id") == batch["id"]:
                    clip.detection_lineage["reviewed_at"] = ""
                    clip.detection_lineage[
                        "false_positive_confirmed_at"] = ""
                    clip.detection_lineage[
                        "recovery_withdrawn_at"] = ""
                    clip.detection_lineage["review_batch_id"] = ""
        try:
            self.autodetect_repo.cancel_review_batch(
                batch["id"],
                confirmation={
                    "cancelled_by_user": True,
                    "scope": "contiguous_source_range",
                },
                commit=False,
            )
            self.commit()
        except Exception:
            self.conn.rollback()
            self.clips = before_clips
            self._undo_stack = before_undo
            self._redo_stack = before_redo
            self.dirty = before_dirty
            self._capture_description = before_description
            self.project.updated_at = before_project_updated_at
            raise
        cancelled = self.autodetect_repo.get_review_batch(batch["id"])
        assert cancelled is not None
        return cancelled

    def _assert_capture_matches_current_source(
        self,
        session: dict[str, Any],
    ) -> None:
        source = session.get("source", {})
        captured_file = source.get("source", {})
        captured_path = (
            str(captured_file.get("path", ""))
            if isinstance(captured_file, dict) else ""
        )
        current_path = str(self.project.source_video_path or "")
        if not captured_path or not current_path:
            raise DatabaseError(
                "The autodetect capture has no verifiable source identity.",
                "Run Detect Plays on the currently loaded film first.",
            )
        left = str(Path(captured_path).resolve(strict=False)).replace(
            "\\", "/").casefold()
        right = str(Path(current_path).resolve(strict=False)).replace(
            "\\", "/").casefold()
        if left != right:
            raise DatabaseError(
                "The latest autodetect run belongs to a different source.",
                "Run Detect Plays on the currently loaded film first.",
            )
        try:
            current_stat = Path(current_path).stat()
        except OSError:
            raise DatabaseError(
                "The source file for the active test batch is missing or "
                "unreadable.",
                "Restore the original file at its captured path, or cancel "
                "the batch and run detection again.",
            )
        captured_size = (
            captured_file.get("size_bytes")
            if isinstance(captured_file, dict) else None
        )
        if captured_size is not None:
            if int(captured_size) != int(current_stat.st_size):
                raise DatabaseError(
                    "The current source file does not match the autodetect "
                    "capture.",
                    "Run Detect Plays on the currently loaded film first.",
                )
        captured_mtime = (
            captured_file.get("mtime_ns")
            if isinstance(captured_file, dict) else None
        )
        if (
            captured_mtime is not None
            and int(captured_mtime) != int(current_stat.st_mtime_ns)
        ):
            raise DatabaseError(
                "The source file changed after autodetect was run.",
                "Run Detect Plays on the currently loaded film first.",
            )
        captured_duration = source.get("duration_ms")
        if (
            captured_duration is not None
            and self.project.source_duration_ms > 0
            and int(captured_duration) != int(self.project.source_duration_ms)
        ):
            raise DatabaseError(
                "The latest autodetect run has a different source duration.",
                "Run Detect Plays on the currently loaded film first.",
            )

    def _assert_active_batch_source(self) -> dict[str, Any] | None:
        batch = self.autodetect_repo.get_active_review_batch()
        if batch is None:
            return None
        session = self.autodetect_repo.get_session(batch["session_id"])
        if session is None:
            raise DatabaseError("The autodetect capture no longer exists.")
        self._assert_capture_matches_current_source(session)
        return batch

    def latest_autodetect_capture_with_coverage(
            self) -> dict[str, Any] | None:
        """Return the newest coverage-aware capture for the current source."""
        sessions = self.autodetect_repo.list_sessions(self.project.id)
        for captured in reversed(sessions):
            try:
                self._assert_capture_matches_current_source(captured)
            except DatabaseError:
                continue
            result = captured.get("result", {})
            if not isinstance(result, dict):
                return None
            coverage = result.get("coverage")
            if not isinstance(coverage, list):
                # A newer legacy capture supersedes any older ledger; painting
                # the older result would misrepresent the latest detection.
                return None
            return captured
        return None

    def latest_autodetect_coverage(self) -> list[dict[str, Any]]:
        """Return the latest ledger annotated with durable review decisions."""
        captured = self.latest_autodetect_capture_with_coverage()
        if captured is None:
            return []
        coverage = captured["result"]["coverage"]
        reviews = {
            int(item["segment_index"]): item
            for item in self.autodetect_repo.list_coverage_reviews(
                captured["id"])
        }
        gap_audits = coverage_audit_service.audit_possible_missed(captured)
        annotated: list[dict[str, Any]] = []
        for index, segment in enumerate(coverage):
            if not isinstance(segment, dict):
                continue
            item = copy.deepcopy(segment)
            item["segment_index"] = index
            item["review_status"] = ""
            if self._is_recoverable_fragment_segment(item):
                audit = gap_audits.get(index)
                if audit is not None:
                    item["audit_level"] = audit.level
                    item["audit_label"] = audit.label
                decision = reviews.get(index)
                if decision is None:
                    item["review_status"] = "pending"
                elif decision["status"] == "clip_created" \
                        and not self._source_range_is_covered(
                            int(item["start_ms"]), int(item["end_ms"])):
                    # Undo, delete, or a later boundary edit makes any newly
                    # uncovered portion pending again automatically.
                    item["review_status"] = "pending"
                else:
                    item["review_status"] = decision["status"]
                    item["review_clip_id"] = decision.get("clip_id", "")
            annotated.append(item)
        return annotated

    @staticmethod
    def _is_recoverable_fragment_segment(segment: dict[str, Any]) -> bool:
        """Whether source coverage is preserved without a normal play row."""
        return (
            segment.get("kind") == "possible_missed"
            or (
                segment.get("kind") == "review"
                and segment.get("candidate_kind") == "unclassified"
            )
        )

    @staticmethod
    def _is_ignored_detector_clip(clip: Clip) -> bool:
        """Whether a reversible detector row must not block real plays."""
        lineage = clip.detection_lineage
        return bool(
            lineage.get("suppressed_unclassified")
            or (
                not clip.enabled
                and lineage.get("candidate_ids")
                and lineage.get("false_positive_confirmed_at")
            )
            or (
                not clip.enabled
                and lineage.get("recovery_id")
                and lineage.get("recovery_withdrawn_at")
            )
        )

    def _source_range_is_covered(
            self, start_ms: int, end_ms: int) -> bool:
        """Return whether ordinary play ranges continuously cover an interval."""
        start = int(start_ms)
        end = int(end_ms)
        if end <= start:
            return False
        intervals = sorted(
            (
                max(start, clip.start_ms),
                min(end, clip.end_ms),
            )
            for clip in self.clips
            if clip.enabled
            and not self._is_ignored_detector_clip(clip)
            and clip.end_ms > start
            and clip.start_ms < end
        )
        cursor = start
        for interval_start, interval_end in intervals:
            if interval_end <= cursor:
                continue
            if interval_start > cursor:
                return False
            cursor = max(cursor, interval_end)
            if cursor >= end:
                return True
        return False

    def fragment_reclaim_options(
        self,
        *,
        clip_id: str = "",
        at_ms: int | None = None,
    ) -> list[FragmentReclaimOption]:
        """List guarded extensions into pending adjacent source fragments.

        A play edge must already touch or sit inside the fragment. The target
        boundary is then clamped to the first other real clip, so this action
        can never introduce a new overlap.
        """
        target_clip = self.get_clip(clip_id) if clip_id else None
        if clip_id and (
            target_clip is None
            or not target_clip.enabled
            or self._is_ignored_detector_clip(target_clip)
        ):
            return []
        fragments = [
            segment for segment in self.latest_autodetect_coverage()
            if self._is_recoverable_fragment_segment(segment)
            and segment.get("review_status") in {"", "pending"}
        ]
        if at_ms is not None:
            position = int(at_ms)
            fragments = [
                segment for segment in fragments
                if int(segment["start_ms"]) <= position
                < int(segment["end_ms"])
            ]
        targets = [target_clip] if target_clip is not None else [
            clip for clip in self.clips
            if clip.enabled and not self._is_ignored_detector_clip(clip)
        ]
        options: list[FragmentReclaimOption] = []
        for segment in fragments:
            fragment_start = int(segment["start_ms"])
            fragment_end = int(segment["end_ms"])
            segment_index = int(segment["segment_index"])
            for clip in targets:
                if clip is None:
                    continue
                # Absorb a fragment that begins where this play ends, or one
                # whose beginning has already been manually entered.
                if fragment_start <= clip.end_ms < fragment_end:
                    original = clip.end_ms
                    boundary = fragment_end
                    blocker: Clip | None = None
                    for other in self.clips:
                        if other.id == clip.id \
                                or self._is_ignored_detector_clip(other):
                            continue
                        if other.end_ms <= original \
                                or other.start_ms >= fragment_end:
                            continue
                        candidate = max(original, other.start_ms)
                        if candidate < boundary:
                            boundary = candidate
                            blocker = other
                    if boundary > original:
                        options.append(FragmentReclaimOption(
                            segment_index=segment_index,
                            clip_id=clip.id,
                            edge="end",
                            fragment_start_ms=fragment_start,
                            fragment_end_ms=fragment_end,
                            original_boundary_ms=original,
                            target_boundary_ms=boundary,
                            blocker_clip_id=blocker.id if blocker else "",
                            blocker_title=blocker.clip_title if blocker else "",
                        ))

                # Mirror the same rule for a fragment immediately before a
                # play. A previous play's end is the hard lower bound.
                if fragment_start < clip.start_ms <= fragment_end:
                    original = clip.start_ms
                    boundary = fragment_start
                    blocker = None
                    for other in self.clips:
                        if other.id == clip.id \
                                or self._is_ignored_detector_clip(other):
                            continue
                        if other.start_ms >= original \
                                or other.end_ms <= fragment_start:
                            continue
                        candidate = min(original, other.end_ms)
                        if candidate > boundary:
                            boundary = candidate
                            blocker = other
                    if boundary < original:
                        options.append(FragmentReclaimOption(
                            segment_index=segment_index,
                            clip_id=clip.id,
                            edge="start",
                            fragment_start_ms=fragment_start,
                            fragment_end_ms=fragment_end,
                            original_boundary_ms=original,
                            target_boundary_ms=boundary,
                            blocker_clip_id=blocker.id if blocker else "",
                            blocker_title=blocker.clip_title if blocker else "",
                        ))
        return sorted(options, key=lambda option: (
            option.segment_index,
            option.target_boundary_ms,
            option.edge,
            option.clip_id,
        ))

    def reclaim_fragment_into_clip(
        self,
        segment_index: int,
        clip_id: str,
        edge: str,
    ) -> FragmentReclaimOption:
        """Safely extend one play into preserved footage as one undo step."""
        option = next((
            item for item in self.fragment_reclaim_options(clip_id=clip_id)
            if item.segment_index == int(segment_index)
            and item.edge == edge
        ), None)
        if option is None:
            raise DatabaseError(
                "That preserved fragment is no longer adjacent to this play.",
                "Refresh the timeline and choose an available reclaim action.")
        clip = self.get_clip(clip_id)
        captured = self.latest_autodetect_capture_with_coverage()
        if clip is None or captured is None:
            raise DatabaseError(
                "The play or its detection coverage is no longer available.")
        segment = self._possible_missed_segment(captured, segment_index)

        rollback_state = self._capture_checkpoint_rollback_state()
        self.checkpoint("reclaim preserved footage")
        if option.edge == "start":
            clip.start_ms = option.target_boundary_ms
        else:
            clip.end_ms = option.target_boundary_ms
        clip.analysis.pop(PREDICTION_KEY, None)
        clip.touch()
        if self._source_range_is_covered(
                int(segment["start_ms"]), int(segment["end_ms"])):
            self._pending_coverage_review_mutations.append({
                "action": "upsert",
                "review": {
                    "session_id": captured["id"],
                    "segment_index": int(segment_index),
                    "status": "clip_created",
                    "clip_id": clip.id,
                    "reviewed_at": utc_now(),
                },
            })
        try:
            self._commit_after()
        except Exception:
            self._restore_checkpoint_rollback_state(rollback_state)
            raise
        return option

    def autodetect_coverage_review_queue(self) -> dict[str, Any] | None:
        """Build the everyday review queue without changing detector truth."""
        captured = self.latest_autodetect_capture_with_coverage()
        if captured is None:
            return None
        coverage = self.latest_autodetect_coverage()
        gap_audits = coverage_audit_service.audit_possible_missed(captured)
        items: list[dict[str, Any]] = []
        possible_missed_ms = 0
        for segment in coverage:
            if not self._is_recoverable_fragment_segment(segment):
                continue
            start_ms = int(segment["start_ms"])
            end_ms = int(segment["end_ms"])
            possible_missed_ms += max(0, end_ms - start_ms)
            audit = gap_audits.get(int(segment["segment_index"]))
            audit_data = audit.to_dict() if audit is not None else {
                "level": coverage_audit_service.LOW_SIGNAL,
                "label": coverage_audit_service.AUDIT_LABELS[
                    coverage_audit_service.LOW_SIGNAL],
                "rank": coverage_audit_service.AUDIT_RANKS[
                    coverage_audit_service.LOW_SIGNAL],
                "score": 0,
                "reasons": [
                    "unclaimed source footage with no structural profile"],
                "version": coverage_audit_service.AUDIT_VERSION,
            }
            items.append({
                "item_kind": "possible_missed",
                "segment_index": int(segment["segment_index"]),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "reason": str(segment.get("reason", "")),
                "status": segment.get("review_status", "pending"),
                "clip_id": segment.get("review_clip_id", ""),
                "attention_level": audit_data["level"],
                "attention_label": audit_data["label"],
                "attention_rank": audit_data["rank"],
                "attention_score": audit_data["score"],
                "attention_reasons": audit_data["reasons"],
                "attention_reason": "; ".join(audit_data["reasons"][:2]),
                "audit_version": audit_data["version"],
            })

        candidates = self.autodetect_repo.list_candidates(captured["id"])
        for candidate in candidates:
            if (
                not candidate.get("needs_review")
                or candidate.get("candidate_kind") == "unclassified"
            ):
                continue
            candidate_id = candidate["id"]
            initial_clip_id = str(candidate.get("initial_clip_id", ""))
            linked = [
                clip for clip in self.clips
                if (
                    clip.detection_lineage.get("session_id") == captured["id"]
                    and candidate_id in set(
                        clip.detection_lineage.get("candidate_ids", []))
                )
                or clip.id == initial_clip_id
            ]
            enabled = [clip for clip in linked if clip.enabled]
            reviewed = bool(linked) and all(
                clip.detection_lineage.get("reviewed_at")
                for clip in linked
            )
            items.append({
                "item_kind": "candidate",
                "candidate_id": candidate_id,
                "start_ms": int(candidate["created_start_ms"]),
                "end_ms": int(candidate["created_end_ms"]),
                "reason": str(candidate.get("review_reason", "")),
                "status": "reviewed" if reviewed else "pending",
                "clip_id": enabled[0].id if enabled else "",
                "attention_level": "required",
                "attention_label": "REQUIRED",
                "attention_rank": 1,
                "attention_score": 0,
                "attention_reasons": [
                    str(candidate.get("review_reason", ""))
                    or "the detector marked this candidate as uncertain"],
                "attention_reason":
                    str(candidate.get("review_reason", ""))
                    or "the detector marked this candidate as uncertain",
                "audit_version": "",
            })

        items.sort(key=lambda item: (
            0 if item["status"] == "pending" else 1,
            int(item.get("attention_rank", 99)),
            item["start_ms"],
            item["end_ms"],
        ))
        result = captured.get("result", {})
        plays = result.get("plays", []) if isinstance(result, dict) else []
        summary = {
            "detected_plays": len(plays),
            "needs_review": sum(
                item["item_kind"] == "candidate" for item in items),
            "possible_missed": sum(
                item["item_kind"] == "possible_missed" for item in items),
            "possible_missed_ms": possible_missed_ms,
            "pending": sum(item["status"] == "pending" for item in items),
            "resolved": sum(item["status"] != "pending" for item in items),
            "check_first": sum(
                item["status"] == "pending"
                and item.get("attention_level") ==
                coverage_audit_service.CHECK_FIRST
                for item in items),
            "review_priority": sum(
                item["status"] == "pending"
                and item.get("attention_level") ==
                coverage_audit_service.REVIEW
                for item in items),
            "low_signal": sum(
                item["status"] == "pending"
                and item.get("attention_level") ==
                coverage_audit_service.LOW_SIGNAL
                for item in items),
            "audit_version": coverage_audit_service.AUDIT_VERSION,
            "separators": sum(
                segment.get("kind") == "separator"
                for segment in coverage),
        }
        return {
            "session_id": captured["id"],
            "summary": summary,
            "items": items,
        }

    @classmethod
    def _possible_missed_segment(
        cls,
        captured: dict[str, Any],
        segment_index: int,
    ) -> dict[str, Any]:
        coverage = captured.get("result", {}).get("coverage", [])
        try:
            segment = coverage[int(segment_index)]
        except (IndexError, TypeError, ValueError):
            raise DatabaseError("Coverage review segment was not found.")
        if not isinstance(segment, dict) \
                or not cls._is_recoverable_fragment_segment(segment):
            raise DatabaseError(
                "Only possible-missed footage can use this review action.")
        return segment

    def add_possible_missed_review_clip(
        self,
        segment_index: int,
        clip: Clip,
    ) -> Clip:
        """Create a reversible clip and resolve one possible-missed interval."""
        captured = self.latest_autodetect_capture_with_coverage()
        if captured is None:
            raise DatabaseError(
                "No coverage-aware detection matches the current film.")
        segment = self._possible_missed_segment(captured, segment_index)
        if clip.start_ms != int(segment["start_ms"]) \
                or clip.end_ms != int(segment["end_ms"]):
            raise DatabaseError(
                "The review clip must preserve the complete possible-missed "
                "source range.")
        decisions = {
            int(item["segment_index"]): item
            for item in self.autodetect_repo.list_coverage_reviews(
                captured["id"])
        }
        existing = decisions.get(int(segment_index))
        if existing is not None and (
            existing["status"] == "dismissed"
            or (
                existing["status"] == "clip_created"
                and self._source_range_is_covered(
                    int(segment["start_ms"]), int(segment["end_ms"]))
            )
        ):
            raise DatabaseError(
                "That possible-missed range has already been reviewed.")

        rollback_state = self._capture_checkpoint_rollback_state()
        self.checkpoint("add possible-missed review clip")
        suppressed = next((
            existing_clip for existing_clip in self.clips
            if existing_clip.start_ms == clip.start_ms
            and existing_clip.end_ms == clip.end_ms
            and existing_clip.detection_lineage.get(
                "suppressed_unclassified")
        ), None)
        if suppressed is not None:
            clip = suppressed
            clip.enabled = True
            clip.detection_lineage.pop("suppressed_unclassified", None)
            clip.detection_lineage.pop("suppressed_at", None)
            clip.touch()
        else:
            clip.project_id = self.project.id
            if not clip.clip_number:
                clip.clip_number = self.next_clip_number()
            position = next((
                index for index, existing_clip in enumerate(self.clips)
                if existing_clip.start_ms > clip.start_ms
            ), len(self.clips))
            self.clips.insert(position, clip)
        self._pending_coverage_review_mutations.append({
            "action": "upsert",
            "review": {
                "session_id": captured["id"],
                "segment_index": int(segment_index),
                "status": "clip_created",
                "clip_id": clip.id,
                "reviewed_at": utc_now(),
            },
        })
        try:
            self._commit_after()
        except Exception:
            self._restore_checkpoint_rollback_state(rollback_state)
            raise
        return clip

    def dismiss_possible_missed(self, segment_index: int) -> bool:
        """Record a reversible human decision that an amber gap is not a play."""
        captured = self.latest_autodetect_capture_with_coverage()
        if captured is None:
            raise DatabaseError(
                "No coverage-aware detection matches the current film.")
        segment = self._possible_missed_segment(captured, segment_index)
        existing = next((
            item for item in self.autodetect_repo.list_coverage_reviews(
                captured["id"])
            if int(item["segment_index"]) == int(segment_index)
        ), None)
        if existing is not None and existing["status"] == "dismissed":
            return False
        if existing is not None and existing["status"] == "clip_created" \
                and self._source_range_is_covered(
                    int(segment["start_ms"]),
                    int(segment["end_ms"]),
                ):
            raise DatabaseError(
                "Undo or delete the review clip before marking this range "
                "as not a play.")
        self.autodetect_repo.upsert_coverage_review({
            "session_id": captured["id"],
            "segment_index": int(segment_index),
            "status": "dismissed",
            "clip_id": "",
            "reviewed_at": utc_now(),
        })
        return True

    def restore_possible_missed(self, segment_index: int) -> bool:
        """Return a dismissed or orphaned review decision to pending."""
        captured = self.latest_autodetect_capture_with_coverage()
        if captured is None:
            raise DatabaseError(
                "No coverage-aware detection matches the current film.")
        self._possible_missed_segment(captured, segment_index)
        return bool(self.autodetect_repo.delete_coverage_review(
            captured["id"], int(segment_index)))

    def suppress_legacy_unclassified_clips(self) -> int:
        """Hide old unclassified detector rows while preserving their film.

        Earlier TapeSift versions materialized every unclassified coverage
        segment as a normal clip. The same source ranges already live in the
        immutable coverage ledger, so keeping both created hundreds of
        one-second "plays". This one-time migration disables only clips whose
        stored detector provenance is exclusively unclassified. A later
        Create Review Clip action can reclaim the exact range.
        """
        if self.autodetect_repo.get_active_review_batch() is not None:
            return 0
        unclassified_clip_ids: set[str] = set()
        play_clip_ids: set[str] = set()
        kept_clip_ids: set[str] = set()
        for captured in self.autodetect_repo.list_sessions(self.project.id):
            candidates = self.autodetect_repo.list_candidates(captured["id"])
            try:
                kept = selected_candidate_keys(captured["result"], captured["ui_options"])
            except ValueError:
                # Unreadable new intent cannot justify disabling a human clip.
                kept_clip_ids.update(c["initial_clip_id"] for c in candidates)
                kept = None
            kept_clip_ids.update(
                review["clip_id"] for review in self.autodetect_repo.list_coverage_reviews(captured["id"])
                if review["status"] == "clip_created")
            for candidate in candidates:
                clip_id = str(candidate.get("initial_clip_id", ""))
                if not clip_id:
                    continue
                if candidate.get("candidate_kind") == "unclassified":
                    unclassified_clip_ids.add(clip_id)
                    if kept is not None and ("unclassified", candidate["candidate_index"]) in kept:
                        kept_clip_ids.add(clip_id)
                elif candidate.get("candidate_kind") == "play":
                    play_clip_ids.add(clip_id)
        targets = [
            clip for clip in self.clips
            if clip.id in unclassified_clip_ids
            and clip.id not in play_clip_ids
            and clip.id not in kept_clip_ids
            and not clip.detection_lineage.get("suppressed_unclassified")
        ]
        if not targets:
            return 0
        suppressed_at = utc_now()
        self._describe_detection_change(
            "suppress legacy unclassified detector fragments")
        for clip in targets:
            clip.enabled = False
            clip.detection_lineage["suppressed_unclassified"] = True
            clip.detection_lineage["suppressed_at"] = suppressed_at
            clip.tags = [tag for tag in clip.tags if tag != REVIEW_TAG]
            marker = f"{REVIEW_PREFIX} "
            if clip.clip_title.startswith(marker):
                clip.clip_title = clip.clip_title[len(marker):]
            clip.touch()
        self.commit()
        return len(targets)

    def add_detected_clips(
        self,
        clips: list[Clip],
        candidates: list[dict[str, Any]],
        *,
        detector_id: str,
        detector_version: str,
        app_version: str,
        source: dict[str, Any],
        parameters: dict[str, Any],
        result: dict[str, Any],
        ui_options: dict[str, Any],
        runtime_seconds: float | None,
        provenance: dict[str, Any] | None = None,
    ) -> str:
        """Admit detector clips and preserve immutable provenance atomically.

        Exact detector reruns reuse existing ranges instead of appending a
        second copy. A confident candidate may reuse any exact clip, including
        a disabled false-positive decision. An uncertain candidate only reuses
        a detector-owned clip so a manual clip is never silently turned into a
        detector review item.
        """
        if len(clips) != len(candidates):
            raise ValueError(
                "Detected clips and candidates must be aligned.")
        if self._pending_autodetect_session is not None:
            raise DatabaseError(
                "Another detection capture is waiting to be saved.")
        if self.autodetect_repo.get_active_review_batch() is not None:
            raise DatabaseError(
                "Finish the active autodetect test batch before running "
                "detection again.")
        required_candidate_fields = {
            "candidate_kind", "candidate_index",
            "detector_start_ms", "detector_end_ms",
        }
        if any(
            not isinstance(candidate, dict)
            or not required_candidate_fields <= candidate.keys()
            for candidate in candidates
        ):
            raise ValueError("A detected candidate is missing provenance fields.")
        kept = selected_candidate_keys(result, ui_options)
        if kept is not None:
            actual = [(c["candidate_kind"], c["candidate_index"]) for c in candidates]
            if len(actual) != len(set(actual)) or set(actual) != kept:
                raise ValueError("Captured candidates do not match the explicit kept selection.")
        captured_source = copy.deepcopy(source)
        captured_source["film_id"] = self._capture_film_id(captured_source)
        captured_provenance = copy.deepcopy(provenance or {})
        captured_provenance.setdefault("strength", "advisory")

        before_clips = copy.deepcopy(self.clips)
        before_undo = copy.deepcopy(self._undo_stack)
        before_redo = copy.deepcopy(self._redo_stack)
        before_dirty = self.dirty
        before_description = self._capture_description
        before_pending = self._pending_autodetect_session
        before_project_updated_at = self.project.updated_at
        before_admission = copy.deepcopy(self._last_detection_admission)

        exact_ranges: dict[tuple[int, int], list[Clip]] = {}
        for existing in self.clips:
            exact_ranges.setdefault(
                (existing.start_ms, existing.end_ms), []).append(existing)

        planned_new: dict[tuple[int, int], Clip] = {}
        admissions: list[tuple[Clip, dict[str, Any], bool]] = []
        for clip, proposal in zip(clips, candidates):
            key = (clip.start_ms, clip.end_ms)
            needs_review = bool(proposal.get("needs_review"))
            reusable = [
                existing for existing in exact_ranges.get(key, [])
                if not needs_review
                or bool(existing.detection_lineage.get("candidate_ids"))
            ]
            if reusable:
                target = max(
                    reusable,
                    key=lambda item: (
                        metadata_score(item),
                        -item.order_index,
                        -item.clip_number,
                    ),
                )
                admissions.append((target, proposal, False))
                continue
            if key in planned_new:
                admissions.append((planned_new[key], proposal, False))
                continue
            planned_new[key] = clip
            admissions.append((clip, proposal, True))

        added_count = sum(is_new for _, _, is_new in admissions)
        if added_count:
            self.checkpoint(f"add {added_count} detected clips")
        session_id = new_capture_id("detect")
        created_at = utc_now()
        stored_candidates: list[dict[str, Any]] = []
        next_number = self.next_clip_number()
        added_clip_ids: list[str] = []
        reused_clip_ids: list[str] = []
        for target, proposal, is_new in admissions:
            candidate = dict(proposal)
            candidate_id = new_capture_id("candidate")
            if is_new and not target.clip_number:
                target.clip_number = next_number + len(added_clip_ids)
            target.project_id = self.project.id
            # The factory may clamp at source edges, so persisted applied
            # bounds always reflect the clip the user actually received.
            candidate.update({
                "id": candidate_id,
                "created_start_ms": target.start_ms,
                "created_end_ms": target.end_ms,
                "initial_clip_id": target.id,
                "created_at": created_at,
            })
            if is_new:
                target.detection_lineage = {
                    "schema_version": "1.0",
                    "session_id": session_id,
                    "candidate_ids": [candidate_id],
                    "origin_clip_id": target.id,
                    "derivation": "detected",
                    "reviewed_at": "",
                }
                self.clips.append(target)
                added_clip_ids.append(target.id)
                exact_ranges.setdefault(
                    (target.start_ms, target.end_ms), []).append(target)
            else:
                # A repeated candidate can point to detector truth created
                # earlier in this same admission without creating a duplicate.
                if target.id in added_clip_ids \
                        and target.detection_lineage.get("session_id") \
                        == session_id:
                    target.detection_lineage.setdefault(
                        "candidate_ids", []).append(candidate_id)
                reused_clip_ids.append(target.id)
            stored_candidates.append(candidate)

        self.clips.sort(key=lambda item: item.start_ms)
        self._reindex()
        captured_ui_options = copy.deepcopy(ui_options)
        captured_ui_options["admission"] = {
            "schema_version": "1.0",
            "candidate_count": len(candidates),
            "added_clip_count": len(added_clip_ids),
            "reused_clip_count": len(reused_clip_ids),
            "added_clip_ids": list(added_clip_ids),
            "reused_clip_ids": list(reused_clip_ids),
        }
        session = {
            "id": session_id,
            "project_id": self.project.id,
            "schema_version": "1.0",
            "detector_id": detector_id,
            "detector_version": detector_version,
            "app_version": app_version,
            "source": captured_source,
            "parameters": parameters,
            "result": result,
            "ui_options": captured_ui_options,
            "provenance": captured_provenance,
            "runtime_seconds": runtime_seconds,
            "created_at": created_at,
        }
        self._last_detection_admission = {
            "session_id": session_id,
            "candidate_count": len(candidates),
            "added_clip_ids": list(added_clip_ids),
            "reused_clip_ids": list(reused_clip_ids),
        }
        self._pending_autodetect_session = (session, stored_candidates)
        try:
            self._commit_after()
        except Exception:
            self.clips = before_clips
            self._undo_stack = before_undo
            self._redo_stack = before_redo
            self.dirty = before_dirty
            self._capture_description = before_description
            self._pending_autodetect_session = before_pending
            self.project.updated_at = before_project_updated_at
            self._last_detection_admission = before_admission
            raise
        return session_id

    def last_detection_admission(self) -> dict[str, Any]:
        """Return how the most recent detector proposals entered the project."""
        return copy.deepcopy(self._last_detection_admission)

    def _capture_film_id(self, source: dict[str, Any]) -> str:
        supplied = source.get("film_id")
        if supplied:
            return str(supplied)
        for previous in reversed(
                self.autodetect_repo.list_sessions(self.project.id)):
            previous_source = previous.get("source", {})
            previous_id = previous_source.get("film_id")
            if previous_id and self._same_source_identity(
                    source, previous_source):
                return str(previous_id)
        return new_capture_id("film")

    @staticmethod
    def _same_source_identity(
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> bool:
        left_file = left.get("source", {})
        right_file = right.get("source", {})
        left_size = left_file.get("size_bytes") \
            if isinstance(left_file, dict) else None
        right_size = right_file.get("size_bytes") \
            if isinstance(right_file, dict) else None
        left_mtime = left_file.get("mtime_ns") \
            if isinstance(left_file, dict) else None
        right_mtime = right_file.get("mtime_ns") \
            if isinstance(right_file, dict) else None
        left_duration = left.get("duration_ms")
        right_duration = right.get("duration_ms")
        if (
            left_size is not None
            and right_size is not None
            and left_duration is not None
            and right_duration is not None
        ):
            same = (
                int(left_size) == int(right_size)
                and int(left_duration) == int(right_duration)
            )
            if left_mtime is not None and right_mtime is not None:
                same = same and int(left_mtime) == int(right_mtime)
            return same
        left_path = left_file.get("path") \
            if isinstance(left_file, dict) else ""
        right_path = right_file.get("path") \
            if isinstance(right_file, dict) else ""
        return bool(left_path and left_path == right_path)

    def _describe_detection_change(self, description: str) -> None:
        description = description.strip()
        if not description:
            return
        if not self._capture_description:
            self._capture_description = description
        elif description not in self._capture_description.split("; "):
            self._capture_description += f"; {description}"

    def _invalidate_changed_detection_reviews(
        self,
        before: list[dict[str, Any]],
    ) -> None:
        """Require a fresh confirmation after any non-undo geometry change."""
        previous = {state["clip_id"]: state for state in before}
        for clip in self.clips:
            lineage = clip.detection_lineage
            if not lineage or not lineage.get("reviewed_at"):
                continue
            state = previous.get(clip.id)
            if state is None:
                continue
            if (
                int(clip.start_ms) != int(state["start_ms"])
                or int(clip.end_ms) != int(state["end_ms"])
            ):
                lineage["reviewed_at"] = ""
                lineage["false_positive_confirmed_at"] = ""
                lineage["recovery_withdrawn_at"] = ""

    @staticmethod
    def _detection_state(clips: list[Clip]) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        for clip in clips:
            lineage = clip.detection_lineage
            if not isinstance(lineage, dict):
                continue
            session_id = lineage.get("session_id")
            if not session_id:
                continue
            candidate_ids = lineage.get("candidate_ids", [])
            if isinstance(candidate_ids, str):
                candidate_ids = [candidate_ids]
            states.append({
                "session_id": str(session_id),
                "clip_id": clip.id,
                "candidate_ids": sorted(str(value) for value in candidate_ids),
                "recovery_id": str(lineage.get("recovery_id", "")),
                "recovery_kind": str(lineage.get("recovery_kind", "")),
                "batch_id": str(lineage.get("batch_id", "")),
                "review_batch_id": str(
                    lineage.get("review_batch_id", "")),
                "origin_clip_id": str(
                    lineage.get("origin_clip_id", clip.id)),
                "parent_clip_id": str(
                    lineage.get("parent_clip_id", "")),
                "derivation": str(
                    lineage.get("derivation", "detected")),
                "start_ms": int(clip.start_ms),
                "end_ms": int(clip.end_ms),
                "enabled": bool(clip.enabled),
                "reviewed_at": str(lineage.get("reviewed_at", "")),
                "false_positive_confirmed_at": str(
                    lineage.get("false_positive_confirmed_at", "")),
                "recovery_withdrawn_at": str(
                    lineage.get("recovery_withdrawn_at", "")),
                "review_marker": (
                    REVIEW_TAG in clip.tags
                    or clip.clip_title.startswith(f"{REVIEW_PREFIX} ")
                ),
            })
        return sorted(states, key=lambda state: state["clip_id"])

    @staticmethod
    def _state_for_session(
        states: list[dict[str, Any]], session_id: str,
    ) -> list[dict[str, Any]]:
        return [
            state for state in states
            if state["session_id"] == session_id
        ]

    @classmethod
    def _changed_detection_sessions(
        cls,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
    ) -> list[str]:
        session_ids = sorted({
            state["session_id"] for state in [*before, *after]
        })
        return [
            session_id for session_id in session_ids
            if cls._state_for_session(before, session_id)
            != cls._state_for_session(after, session_id)
        ]

    @staticmethod
    def _capture_action(description: str) -> str:
        value = description.lower()
        if value.startswith("undo "):
            return "undo"
        if value.startswith("redo "):
            return "redo"
        if "cancel autodetect" in value:
            return "cancel_batch"
        if "reviewed" in value:
            return "review"
        if "withdraw" in value and "missed autodetect" in value:
            return "withdraw_recovery"
        if "missed autodetect" in value:
            return "recover_miss"
        if "false positive" in value:
            return "confirm_false_positive"
        if "detected clips" in value:
            return "create"
        if "split" in value:
            return "split"
        if "duplicate" in value:
            return "duplicate"
        if "delete" in value:
            return "delete"
        if "exclude" in value:
            return "exclude"
        if "restore" in value:
            return "restore"
        if "trim" in value or "boundary" in value:
            return "trim"
        return "edit"

    def _reindex(self) -> None:
        for index, clip in enumerate(self.clips):
            clip.order_index = index
        self.dirty = True
