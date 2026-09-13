"""An unclosed synthetic project must not leak recovery into the next test."""

import pytest

from tapesift.services import recovery_service


@pytest.mark.parametrize("project_name", ["First project", "Next project"])
def test_recovery_starts_clean_and_tracks_this_test(tmp_path, project_name):
    assert recovery_service.pending_recovery() is None
    project = tmp_path / "game.tapesift"
    project.write_bytes(b"synthetic project")
    recovery_service.mark_open(project, project_name)
    assert recovery_service.pending_recovery() == (str(project), project_name)
    # Deliberately leave this marker open, as an interrupted UI test does.
