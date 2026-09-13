"""Evidence is portable, independent of machine metadata and reversible."""
import struct
import zlib

import pytest

from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession, duplicate_project, edit_clip_metadata
from tapesift.services.source_photo import make_photo, photo_warnings, source_identity, time_relationship, validate_photo


def png_bytes():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x00\xff\x00")) + chunk(b"IEND", b""))


@pytest.fixture
def saved(tmp_path):
    source = tmp_path / "film.mp4"
    source.write_bytes(b"Unit-test source identity only; not media evidence")
    session = ProjectSession.create("photos", tmp_path, tmp_path / "exports")
    session.project.source_video_path = str(source)
    clip = session.add_clip(Clip(1000, 3000, details={"custom": "  exact  "},
        analysis={"machine": "kept"}, thumbnail_path="cache.jpg"))
    identity = source_identity(str(source))
    data = png_bytes()
    photo = make_photo(clip, data, 700, identity, identity)
    session.set_source_photo(clip.id, photo, data)
    yield session, clip.id, photo, data, source
    session.conn.close()


def test_photo_survives_save_reopen_project_copy_and_unrelated_metadata(saved):
    session, clip_id, photo, data, source = saved
    clip = session.get_clip(clip_id)
    assert clip.details == {"custom": "  exact  "} and clip.analysis == {"machine": "kept"}
    assert clip.thumbnail_path == "cache.jpg" and not clip.notes and not clip.tags
    session.close()
    reopened = ProjectSession.open(session.db_path)
    try:
        assert reopened.get_clip(clip_id).source_photo == photo
        assert reopened.get_clip(clip_id).source_photo_png == data
    finally:
        reopened.close()
    copied = ProjectSession.open(duplicate_project(session.db_path))
    try:
        assert copied.get_clip(clip_id).source_photo_png == data
        assert copied.get_clip(clip_id).source_photo == photo
    finally:
        copied.close()
    # The closed-project Library writer must retain evidence outside its schema.
    edit_clip_metadata(session.db_path, clip_id, clip_title="", tags=[], notes="Library note", details={})
    check = ProjectSession.open(session.db_path)
    try:
        restored = check.get_clip(clip_id)
        assert restored.notes == "Library note"
        assert restored.source_photo == photo and restored.source_photo_png == data
    finally:
        check.close()


def test_replace_remove_undo_redo_and_operation_identity(saved):
    session, clip_id, photo, data, source = saved
    session.set_source_photo(clip_id, {}, b"")
    assert not session.get_clip(clip_id).source_photo_png
    session.undo()
    assert session.get_clip(clip_id).source_photo == photo
    session.redo()
    assert not session.get_clip(clip_id).source_photo
    session.undo()
    replacement = dict(photo, position_ms=1500)
    session.set_source_photo(clip_id, replacement, data)
    session.undo()
    assert session.get_clip(clip_id).source_photo == photo
    duplicate = session.duplicate_clip(clip_id)
    assert duplicate.source_photo["clip_id"] == duplicate.id
    assert duplicate.source_photo["captured_clip_id"] == clip_id
    assert duplicate.source_photo_png == data
    duplicate.source_photo["position_ms"] = 1900
    assert session.get_clip(clip_id).source_photo["position_ms"] == 700
    session.undo()
    tail = session.split_clip(clip_id, 2000)
    assert not tail.source_photo and not tail.source_photo_png
    head = session.get_clip(clip_id)
    assert head.source_photo == photo and head.source_photo_png == data
    assert "range changed" in " ".join(photo_warnings(photo, head, str(source)))
    merged = session.merge_clips([head.id, tail.id])
    assert not merged.source_photo and not merged.source_photo_png
    session.undo()
    assert session.get_clip(clip_id).source_photo == photo
    session.undo()
    assert photo_warnings(photo, session.get_clip(clip_id), str(source)) == []


def test_failed_write_read_only_and_invalid_record_preserve_previous_photo(saved, monkeypatch):
    session, clip_id, photo, data, source = saved
    undo_size = len(session._undo_stack)
    def fail(*_args, **_kwargs):
        raise OSError("disk failure")
    with monkeypatch.context() as patch:
        patch.setattr(session.clip_repo, "save_many", fail)
        with pytest.raises(OSError): session.set_source_photo(clip_id, {}, b"")
    assert len(session._undo_stack) == undo_size
    assert session.get_clip(clip_id).source_photo == photo
    assert session.get_clip(clip_id).source_photo_png == data
    read_only = ProjectSession.open_read_only(session.db_path)
    try:
        with pytest.raises(Exception, match="read-only"):
            read_only.set_source_photo(clip_id, {}, b"")
    finally:
        read_only.close()
    invalid = dict(photo, clip_id="another play")
    with pytest.raises(Exception, match="identity"):
        session.set_source_photo(clip_id, invalid, data)
    assert len(session._undo_stack) == undo_size
    assert session.get_clip(clip_id).source_photo == photo


def test_source_or_bounds_change_keeps_historical_pixels_and_never_fills_values(saved):
    session, clip_id, photo, data, source = saved
    clip = session.get_clip(clip_id)
    assert time_relationship(700, clip) == "before clip"
    assert time_relationship(1000, clip) == "within clip"
    assert time_relationship(3000, clip) == "after clip"
    source.write_bytes(b"Replaced unit-test source")
    assert "film changed" in " ".join(photo_warnings(photo, clip, str(source)))
    source.unlink()
    assert "unavailable" in " ".join(photo_warnings(photo, clip, str(source)))
    assert clip.source_photo_png == data and clip.details == {"custom": "  exact  "}
    assert validate_photo(photo, data, clip_id) == ""
    for changed, png in [(dict(photo, position_ms=-1), data), (dict(photo, width=999999), data),
            (dict(photo, position_ms=True), data), (dict(photo, frame_source={}), data),
            (photo, data[:-1]), (photo, b""), ({}, data)]:
        assert validate_photo(changed, png, clip_id)
    session.conn.execute("UPDATE clips SET source_photo_json='broken',source_photo_png=? WHERE id=?", (data, clip_id))
    session.conn.commit()
    restored = session.clip_repo.list_for_project(session.project.id)[0]
    assert restored.source_photo == {} and restored.source_photo_png == data


def test_live_library_writer_and_later_save_preserve_source_evidence(saved):
    from types import SimpleNamespace
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow

    session, clip_id, photo, data, source = saved
    refreshed = []
    owner = SimpleNamespace(session=session, _project_is_open=lambda path:path==str(session.db_path),
        _refresh_clip_list=lambda:refreshed.append(True))
    result = MainWindowWorkflow._apply_library_edit(owner, str(session.db_path), clip_id,
        {"clip_title":"", "tags":[], "notes":"Live Library note", "details":{}})
    assert result == (True, "") and refreshed == [True]
    session.save()
    restored = session.clip_repo.list_for_project(session.project.id)[0]
    assert restored.notes == "Live Library note" and restored.details == {"custom":"  exact  "}
    assert restored.source_photo == photo and restored.source_photo_png == data
    session.undo()
    assert session.get_clip(clip_id).notes == ""
    assert session.get_clip(clip_id).source_photo == photo and session.get_clip(clip_id).source_photo_png == data
