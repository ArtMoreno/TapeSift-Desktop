"""Shared-folder snapshots preserve history and open only verified local copies."""
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

import pytest

from tapesift.database.migrations import MIGRATIONS
from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.services import project_sync_service as sync


@pytest.fixture
def project(tmp_path):
    session = ProjectSession.create("Game One", tmp_path / "local", tmp_path / "exports")
    session.project.game_year = "2026"
    session.project.opponent = "Duke"
    session.project.source_video_path = "C:/other-computer/film.mp4"
    session.add_clip(Clip(0, 6000, notes="Original note", details={
        "quarterback": "Darian Mensah", "timing_snap_ms": "2500", "timing_snap_confirmed": "1"}))
    session.save()
    yield session
    session.conn.close()


@pytest.fixture
def store(tmp_path):
    folder = tmp_path / "drive"
    folder.mkdir()
    return sync.SyncStore(folder)


def files(store, revision):
    base = store.root / revision.project_id / revision.revision_id
    return base.with_suffix(".json"), base.with_suffix(".tapesift")


def modify_manifest(store, revision, **changes):
    manifest, _ = files(store, revision)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data.update(changes)
    manifest.write_text(json.dumps(data), encoding="utf-8")


def test_wal_snapshot_is_self_contained_and_reopens_without_local_mapping_side_effects(project, store, tmp_path):
    assert project.db_path.with_name(project.db_path.name + "-wal").is_file()
    project.clips[0].analysis = {"snap_prediction": {"source_ms": 1000}}
    project.clips[0].source_photo_png = b"source evidence travels with the project"
    project.save()
    revision = store.publish(project.db_path)
    manifest, snapshot = files(store, revision)
    assert snapshot.stat().st_size > 0 and manifest.is_file()
    assert set(path.suffix for path in snapshot.parent.iterdir()) == {".json", ".tapesift"}
    assert store.validate_revision(revision.project_id, revision.revision_id) == revision
    local = store.checkout(revision.project_id, revision.revision_id, tmp_path / "second-computer")
    reopened = ProjectSession.open(local)
    try:
        assert reopened.project.game_year == "2026" and reopened.project.opponent == "Duke"
        assert reopened.project.source_video_path == "C:/other-computer/film.mp4"
        assert reopened.project.output_folder == project.project.output_folder
        assert reopened.clips[0].details["timing_snap_ms"] == "2500"
        assert reopened.clips[0].details["quarterback"] == "Darian Mensah"
        assert reopened.clips[0].analysis == project.clips[0].analysis
        assert reopened.clips[0].source_photo_png == project.clips[0].source_photo_png
    finally:
        reopened.conn.close()
    assert store.status(revision.project_id, revision.revision_id).has_updates is False
    assert store.list_projects()[0].latest == revision


def test_sequential_revisions_follow_ancestry_and_skip_identical_content(project, store):
    first = store.publish(project.db_path)
    assert store.publish(project.db_path, project_id=first.project_id, parent_revision=first.revision_id) == first
    project.clips[0].notes = "Saved on the first computer"
    project.save()
    second = store.publish(project.db_path, project_id=first.project_id, parent_revision=first.revision_id)
    assert second.sha256 != first.sha256 and second.parents == (first.revision_id,)
    modify_manifest(store, second, created_at="2000-01-01T00:00:00+00:00")
    status = store.status(first.project_id, first.revision_id)
    assert status.latest.revision_id == second.revision_id
    assert status.has_updates and not status.diverged
    assert len(store.revisions(first.project_id)) == 2


def test_divergent_computers_keep_both_heads_and_every_original_snapshot(project, store, tmp_path):
    first = store.publish(project.db_path)
    original = files(store, first)[1].read_bytes()
    other_path = store.checkout(first.project_id, first.revision_id, tmp_path / "other")
    project.clips[0].notes = "First computer's edit"
    project.save()
    second = store.publish(project.db_path, project_id=first.project_id, parent_revision=first.revision_id)
    other = ProjectSession.open(other_path)
    try:
        other.clips[0].notes = "Second computer's independent edit"
        other.save()
        third = store.publish(other_path, project_id=first.project_id, parent_revision=first.revision_id)
    finally:
        other.conn.close()
    status = store.status(first.project_id, first.revision_id)
    assert status.diverged and status.has_updates and status.latest is None
    assert {revision.revision_id for revision in status.heads} == {second.revision_id, third.revision_id}
    assert files(store, first)[1].read_bytes() == original
    for revision, note in ((second, "First computer's edit"), (third, "Second computer's independent edit")):
        copy = store.checkout(first.project_id, revision.revision_id, tmp_path / "recovery")
        reopened = ProjectSession.open_read_only(copy)
        try:
            assert reopened.clips[0].notes == note
        finally:
            reopened.close()


def test_checkout_never_uses_remote_names_as_paths_or_overwrites_a_local_copy(project, store, tmp_path):
    project.project.name = "../../outside \\ C:\\outside"
    project.save()
    revision = store.publish(project.db_path)
    modify_manifest(store, revision, snapshot_path="../../outside.tapesift", output_path="C:/outside")
    local = tmp_path / "downloads"
    one = store.checkout(revision.project_id, revision.revision_id, local)
    before = one.read_bytes()
    two = store.checkout(revision.project_id, revision.revision_id, local)
    assert one != two and one.parent == two.parent == local
    assert one.read_bytes() == two.read_bytes() == before
    assert not (tmp_path / "outside.tapesift").exists()


def test_each_independent_share_gets_a_separate_project_uuid(project, store):
    one, two = store.publish(project.db_path), store.publish(project.db_path)
    assert one.project_id != two.project_id
    assert len(store.list_projects()) == 2


def test_one_incomplete_project_does_not_hide_other_valid_projects(project, store):
    good, incomplete = store.publish(project.db_path), store.publish(project.db_path)
    files(store, incomplete)[0].unlink()
    projects = {project.project_id: project for project in store.list_projects()}
    assert projects[good.project_id].latest == good and not projects[good.project_id].problem
    assert projects[incomplete.project_id].problem
    assert projects[incomplete.project_id].heads == () and projects[incomplete.project_id].latest is None


@pytest.mark.parametrize("damage", ["missing_file", "bad_manifest", "missing_parent"])
def test_complete_history_remains_browsable_without_claiming_partial_head_is_synced(project, store, tmp_path, damage):
    first = store.publish(project.db_path)
    project.clips[0].notes = "A later revision is transferring"
    project.save()
    second = store.publish(project.db_path, project_id=first.project_id, parent_revision=first.revision_id)
    if damage == "missing_file":
        files(store, second)[1].unlink()
    elif damage == "bad_manifest":
        files(store, second)[0].write_text("{")
    else:
        modify_manifest(store, second, parents=[str(uuid4())])
    with pytest.raises(sync.IncompleteTransferError):
        store.status(first.project_id, first.revision_id)
    assert store.revisions(first.project_id, complete_only=True) == [first]
    reopened = ProjectSession.open_read_only(store.checkout(first.project_id, first.revision_id, tmp_path / "recovery"))
    try:
        assert reopened.clips[0].notes == "Original note"
    finally:
        reopened.close()


def test_missing_or_unmounted_root_is_never_recreated(tmp_path, project):
    root = tmp_path / "missing"
    with pytest.raises(sync.SyncError):
        sync.SyncStore(root)
    assert not root.exists()
    root.mkdir()
    store = sync.SyncStore(root)
    root.rename(tmp_path / "disconnected")
    with pytest.raises(sync.SyncError):
        store.publish(project.db_path)
    assert not root.exists()


def test_live_databases_cannot_be_published_or_checked_out_inside_store(project, store):
    revision = store.publish(project.db_path)
    with pytest.raises(sync.SyncError, match="outside"):
        store.publish(files(store, revision)[1])
    with pytest.raises(sync.SyncError, match="outside"):
        store.checkout(revision.project_id, revision.revision_id, store.root / "local")
    assert not (store.root / "local").exists()


@pytest.mark.parametrize("damage", ["snapshot_missing", "manifest_missing", "partial_json", "partial_snapshot", "missing_parent"])
def test_incomplete_transfers_are_never_reported_synced(project, store, damage):
    revision = store.publish(project.db_path)
    manifest, snapshot = files(store, revision)
    if damage == "snapshot_missing":
        snapshot.unlink()
    elif damage == "manifest_missing":
        manifest.unlink()
    elif damage == "partial_json":
        manifest.write_text('{"format_version":')
    elif damage == "partial_snapshot":
        snapshot.write_bytes(snapshot.read_bytes()[:100])
    else:
        modify_manifest(store, revision, parents=[str(uuid4())])
    with pytest.raises(sync.IncompleteTransferError):
        store.status(revision.project_id, revision.revision_id)


def test_polling_reads_metadata_but_checkout_rejects_same_size_corruption(project, store, tmp_path, monkeypatch):
    revision = store.publish(project.db_path)
    _, snapshot = files(store, revision)
    data = bytearray(snapshot.read_bytes())
    data[-1] ^= 1
    snapshot.write_bytes(data)
    def no_validation(_path):
        raise AssertionError("Polling must not run SQLite integrity checks")
    monkeypatch.setattr(sync, "_validate_database", no_validation)
    assert store.status(revision.project_id, revision.revision_id).latest == revision
    assert store.list_projects()[0].latest == revision
    with pytest.raises(sync.InvalidSnapshotError, match="checksum"):
        store.checkout(revision.project_id, revision.revision_id, tmp_path / "downloads")
    assert not list((tmp_path / "downloads").iterdir())


def test_identical_publish_does_not_claim_a_corrupted_remote_snapshot_is_verified(project, store):
    revision = store.publish(project.db_path)
    _, snapshot = files(store, revision)
    content = bytearray(snapshot.read_bytes())
    content[-1] ^= 1
    snapshot.write_bytes(content)
    with pytest.raises(sync.InvalidSnapshotError, match="checksum"):
        store.publish(project.db_path, project_id=revision.project_id, parent_revision=revision.revision_id)


def test_cyclic_history_is_rejected_even_when_each_snapshot_is_available(project, store):
    first = store.publish(project.db_path)
    project.clips[0].notes = "Next revision"
    project.save()
    second = store.publish(project.db_path, project_id=first.project_id, parent_revision=first.revision_id)
    modify_manifest(store, first, parents=[second.revision_id])
    with pytest.raises(sync.InvalidSnapshotError, match="cycle"):
        store.status(first.project_id)
    assert store.revisions(first.project_id, complete_only=True) == []


def test_shared_project_symlink_cannot_redirect_access_outside_store(store, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    project_id = str(uuid4())
    try:
        (store.root / project_id).symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host")
    with pytest.raises(sync.InvalidSnapshotError, match="redirect"):
        store.revisions(project_id)
    assert not list(external.iterdir())


@pytest.mark.parametrize("damage", ["not_sqlite", "wrong_schema", "future_schema", "trigger"])
def test_matching_checksum_still_requires_valid_supported_sqlite(project, store, tmp_path, damage):
    revision = store.publish(project.db_path)
    _, snapshot = files(store, revision)
    if damage == "not_sqlite":
        snapshot.write_bytes(b"This is not a database")
    else:
        with sqlite3.connect(snapshot) as conn:
            if damage == "wrong_schema":
                conn.execute("ALTER TABLE projects ADD COLUMN surprise TEXT")
            elif damage == "future_schema":
                conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
            else:
                conn.execute("CREATE TRIGGER surprise AFTER UPDATE ON projects BEGIN DELETE FROM clips; END")
    modify_manifest(store, revision, sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                    size_bytes=snapshot.stat().st_size)
    with pytest.raises(sync.InvalidSnapshotError):
        store.checkout(revision.project_id, revision.revision_id, tmp_path / "downloads")
    assert not list((tmp_path / "downloads").iterdir())


@pytest.mark.parametrize("identifier", ["../escape", "C:/escape", "", "f" * 32])
def test_untrusted_identifiers_cannot_select_filesystem_paths(store, identifier):
    with pytest.raises(sync.InvalidSnapshotError):
        store.revisions(identifier)
    assert list(store.root.iterdir()) == []


def test_revision_collision_does_not_overwrite_or_remove_existing_snapshot(project, store, monkeypatch):
    revision = store.publish(project.db_path)
    originals = {path.name: path.read_bytes() for path in files(store, revision)}
    project.clips[0].notes = "New committed content"
    project.save()
    monkeypatch.setattr(sync, "uuid4", lambda: UUID(revision.revision_id))
    with pytest.raises(sync.SyncError):
        store.publish(project.db_path, project_id=revision.project_id, parent_revision=revision.revision_id)
    assert {path.name: path.read_bytes() for path in files(store, revision)} == originals


def test_failed_manifest_publication_leaves_existing_history_intact(project, store, monkeypatch):
    revision = store.publish(project.db_path)
    project.clips[0].notes = "Local changes are retained"
    project.save()
    copy = sync._copy_exclusive
    def fail_manifest(source, target):
        if target.suffix == ".json":
            raise OSError("drive disconnected")
        copy(source, target)
    monkeypatch.setattr(sync, "_copy_exclusive", fail_manifest)
    with pytest.raises(sync.SyncError, match="disconnected"):
        store.publish(project.db_path, project_id=revision.project_id, parent_revision=revision.revision_id)
    assert store.revisions(revision.project_id) == [revision]
    assert project.clips[0].notes == "Local changes are retained"
