"""Coverage Guardian: full-source accounting for automatic detection."""

from tapesift.models.clip import Clip
from tapesift.services import autodetect_capture_service
import tapesift.services.play_detect_service as detector
from tapesift.services.play_detect_service import (
    CoverageSegment,
    DetectedPlay,
    DetectionResult,
    UnclassifiedSegment,
    build_coverage_ledger,
    detect_plays,
    validate_coverage_ledger,
)
from tapesift.services.project_service import ProjectSession


def _assert_exact_partition(
    segments: list[CoverageSegment],
    duration_ms: int,
) -> None:
    validate_coverage_ledger(segments, duration_ms)
    assert segments[0].start_ms == 0
    assert segments[-1].end_ms == duration_ms
    assert sum(segment.duration_ms for segment in segments) == duration_ms


def _reclaim_session(tmp_path, *, next_start_ms: int = 20_000):
    source_path = tmp_path / f"reclaim-{next_start_ms}.mp4"
    source_path.touch()
    source_stat = source_path.stat()
    session = ProjectSession.create(
        f"Reclaim {next_start_ms}", tmp_path, tmp_path / "exports")
    session.project.source_video_path = str(source_path)
    session.project.source_duration_ms = 30_000
    first = Clip(start_ms=0, end_ms=10_000, clip_title="First play")
    session.add_detected_clips(
        [first],
        [{
            "candidate_kind": "play",
            "candidate_index": 0,
            "detector_start_ms": 0,
            "detector_end_ms": 10_000,
            "created_start_ms": 0,
            "created_end_ms": 10_000,
            "angle_starts_ms": [0],
            "angle_count": 1,
            "needs_review": False,
            "review_reason": "",
        }],
        detector_id="tapesift-play-detect",
        detector_version="reclaim-test",
        app_version="reclaim-test",
        source={
            "source": {
                "path": str(source_path),
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
            },
            "duration_ms": 30_000,
        },
        parameters={},
        result={
            "schema_version": "1.0",
            "coverage_schema_version": "1.0",
            "coverage": [
                {
                    "start_ms": 0,
                    "end_ms": 10_000,
                    "kind": "play",
                    "candidate_kind": "play",
                    "candidate_index": 0,
                },
                {
                    "start_ms": 10_000,
                    "end_ms": 20_000,
                    "kind": "possible_missed",
                    "reason": "preserved detector fragment",
                    "candidate_kind": "",
                    "candidate_index": None,
                },
                {
                    "start_ms": 20_000,
                    "end_ms": 30_000,
                    "kind": "play",
                    "candidate_kind": "play",
                    "candidate_index": 1,
                },
            ],
            "plays": [{"start_ms": 0, "end_ms": 10_000}],
        },
        ui_options={},
        runtime_seconds=0.01,
    )
    following = session.add_clip(Clip(
        start_ms=next_start_ms,
        end_ms=30_000,
        clip_title="Following play",
    ))
    return session, first, following


def test_ledger_accounts_for_play_review_black_and_unclaimed_footage() -> None:
    ledger = build_coverage_ledger(
        10_000,
        [DetectedPlay(1_000, 4_000)],
        [UnclassifiedSegment(6_000, 8_000, 1, "check this section")],
        [(0.0, 1.0), (4.0, 5.0)],
    )

    _assert_exact_partition(ledger, 10_000)
    assert [
        (item.start_ms, item.end_ms, item.kind)
        for item in ledger
    ] == [
        (0, 1_000, detector.COVERAGE_SEPARATOR),
        (1_000, 4_000, detector.COVERAGE_PLAY),
        (4_000, 5_000, detector.COVERAGE_SEPARATOR),
        (5_000, 6_000, detector.COVERAGE_POSSIBLE_MISSED),
        (6_000, 8_000, detector.COVERAGE_REVIEW),
        (8_000, 10_000, detector.COVERAGE_POSSIBLE_MISSED),
    ]
    assert ledger[1].candidate_kind == "play"
    assert ledger[3].candidate_kind == ""
    assert ledger[4].candidate_kind == "unclassified"


def test_review_play_wins_over_confident_overlap_and_keeps_reference() -> None:
    ledger = build_coverage_ledger(
        5_000,
        [
            DetectedPlay(0, 5_000),
            DetectedPlay(
                1_000,
                2_000,
                needs_review=True,
                review_reason="ambiguous camera transition",
            ),
        ],
        [],
        [],
    )

    _assert_exact_partition(ledger, 5_000)
    assert [
        (item.start_ms, item.end_ms, item.kind, item.candidate_index)
        for item in ledger
    ] == [
        (0, 1_000, detector.COVERAGE_PLAY, 0),
        (1_000, 2_000, detector.COVERAGE_REVIEW, 1),
        (2_000, 5_000, detector.COVERAGE_PLAY, 0),
    ]


def test_black_intervals_are_clipped_and_rounded_to_source_edges() -> None:
    ledger = build_coverage_ledger(
        1_001,
        [],
        [],
        [(-0.2, 0.5006), (0.9996, 2.0)],
    )

    _assert_exact_partition(ledger, 1_001)
    assert [
        (item.start_ms, item.end_ms, item.kind)
        for item in ledger
    ] == [
        (0, 501, detector.COVERAGE_SEPARATOR),
        (501, 1_000, detector.COVERAGE_POSSIBLE_MISSED),
        (1_000, 1_001, detector.COVERAGE_SEPARATOR),
    ]


def test_short_content_span_is_possible_missed_not_silently_dropped(
        monkeypatch, tmp_path) -> None:
    source = tmp_path / "short-span.mp4"
    source.touch()
    monkeypatch.setattr(
        detector,
        "_run_ffmpeg_analysis",
        lambda *_args: (
            "",
            "\n".join([
                "black_start:10.000 black_end:10.200",
                "black_start:12.000 black_end:12.200",
            ]),
        ),
    )

    result = detect_plays("ffmpeg", source, duration_ms=30_000)

    _assert_exact_partition(result.coverage_segments, 30_000)
    assert [(play.start_ms, play.end_ms) for play in result.plays] == [
        (0, 10_000),
        (12_200, 30_000),
    ]
    assert any(
        item.start_ms == 10_200
        and item.end_ms == 12_000
        and item.kind == detector.COVERAGE_POSSIBLE_MISSED
        for item in result.coverage_segments
    )
    assert result.possible_missed_ms == 1_800
    assert result.separator_ms == 400
    assert result.unaccounted_ms == 0
    assert result.accounted_pct == 100.0


def test_capture_serializes_complete_coverage_without_changing_v1_result() -> None:
    result = DetectionResult(
        plays=[DetectedPlay(0, 4_000)],
        signal="black",
        spans_found=2,
        separators_found=1,
        duration_ms=5_000,
        coverage_segments=build_coverage_ledger(
            5_000,
            [DetectedPlay(0, 4_000)],
            [],
            [(4.0, 5.0)],
        ),
    )

    captured = autodetect_capture_service.serialize_detection_result(result)

    assert captured["schema_version"] == "1.0"
    assert captured["coverage_schema_version"] == "1.0"
    assert captured["summary"]["accounted_ms"] == 5_000
    assert captured["summary"]["unaccounted_ms"] == 0
    assert captured["summary"]["coverage_by_kind"] == {
        detector.COVERAGE_PLAY: 4_000,
        detector.COVERAGE_REVIEW: 0,
        detector.COVERAGE_POSSIBLE_MISSED: 0,
        detector.COVERAGE_SEPARATOR: 1_000,
    }
    assert captured["coverage"][-1]["kind"] == \
        detector.COVERAGE_SEPARATOR


def test_detected_capture_appends_without_replacing_manual_clips(tmp_path) -> None:
    session = ProjectSession.create(
        "Coverage Safety", tmp_path, tmp_path / "exports")
    manual = Clip(
        start_ms=500,
        end_ms=2_500,
        clip_title="Hand-marked play",
    )
    session.add_clip(manual)
    detected = Clip(
        start_ms=5_000,
        end_ms=9_000,
        clip_title="Detected play",
    )

    session.add_detected_clips(
        [detected],
        [{
            "candidate_kind": "play",
            "candidate_index": 0,
            "detector_start_ms": 5_000,
            "detector_end_ms": 9_000,
            "created_start_ms": 5_000,
            "created_end_ms": 9_000,
            "angle_starts_ms": [5_000],
            "angle_count": 1,
            "needs_review": False,
            "review_reason": "",
        }],
        detector_id="tapesift-play-detect",
        detector_version="coverage-test",
        app_version="coverage-test",
        source={"duration_ms": 10_000},
        parameters={},
        result={
            "schema_version": "1.0",
            "coverage_schema_version": "1.0",
        },
        ui_options={},
        runtime_seconds=0.01,
    )

    assert session.get_clip(manual.id) is manual
    assert session.get_clip(manual.id).detection_lineage == {}
    assert {clip.id for clip in session.clips} == {manual.id, detected.id}
    session.close()


def test_coverage_only_detection_persists_without_materializing_clips(
        tmp_path) -> None:
    """A zero-play result still keeps every source interval recoverable."""
    source_path = tmp_path / "all-fragments.mp4"
    source_path.touch()
    source_stat = source_path.stat()
    session = ProjectSession.create(
        "Coverage Only", tmp_path, tmp_path / "exports")
    session.project.source_video_path = str(source_path)
    session.project.source_duration_ms = 10_000

    session_id = session.add_detected_clips(
        [],
        [],
        detector_id="tapesift-play-detect",
        detector_version="coverage-test",
        app_version="coverage-test",
        source={
            "source": {
                "path": str(source_path),
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
            },
            "duration_ms": 10_000,
        },
        parameters={},
        result={
            "schema_version": "1.0",
            "coverage_schema_version": "1.0",
            "coverage": [{
                "start_ms": 0,
                "end_ms": 10_000,
                "kind": "possible_missed",
                "reason": "no play candidate",
                "candidate_kind": "",
                "candidate_index": None,
            }],
            "plays": [],
        },
        ui_options={},
        runtime_seconds=0.01,
    )

    assert session.clips == []
    assert session.autodetect_repo.get_session(session_id) is not None
    assert session.latest_autodetect_coverage()[0]["review_status"] == \
        "pending"
    assert session.autodetect_coverage_review_queue()["summary"][
        "possible_missed"] == 1
    session.close()


def test_legacy_unclassified_clip_is_hidden_then_reused_for_recovery(
        tmp_path) -> None:
    """Migration removes row clutter without deleting or duplicating footage."""
    source_path = tmp_path / "legacy-fragments.mp4"
    source_path.touch()
    source_stat = source_path.stat()
    session = ProjectSession.create(
        "Legacy Fragments", tmp_path, tmp_path / "exports")
    session.project.source_video_path = str(source_path)
    session.project.source_duration_ms = 10_000
    play = Clip(start_ms=0, end_ms=8_000, clip_title="Play 001")
    fragment = Clip(
        start_ms=8_000,
        end_ms=10_000,
        clip_title=f"{detector.REVIEW_PREFIX} Play 002",
        tags=[detector.REVIEW_TAG],
    )
    session.add_detected_clips(
        [play, fragment],
        [
            {
                "candidate_kind": "play",
                "candidate_index": 0,
                "detector_start_ms": 0,
                "detector_end_ms": 8_000,
                "created_start_ms": 0,
                "created_end_ms": 8_000,
                "angle_starts_ms": [0],
                "angle_count": 1,
                "needs_review": False,
                "review_reason": "",
            },
            {
                "candidate_kind": "unclassified",
                "candidate_index": 0,
                "detector_start_ms": 8_000,
                "detector_end_ms": 10_000,
                "created_start_ms": 8_000,
                "created_end_ms": 10_000,
                "angle_starts_ms": [8_000],
                "angle_count": 1,
                "needs_review": True,
                "review_reason": "short uncertain fragment",
            },
        ],
        detector_id="tapesift-play-detect",
        detector_version="legacy-test",
        app_version="legacy-test",
        source={
            "source": {
                "path": str(source_path),
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
            },
            "duration_ms": 10_000,
        },
        parameters={},
        result={
            "schema_version": "1.0",
            "coverage_schema_version": "1.0",
            "coverage": [
                {
                    "start_ms": 0,
                    "end_ms": 8_000,
                    "kind": "play",
                    "candidate_kind": "play",
                    "candidate_index": 0,
                },
                {
                    "start_ms": 8_000,
                    "end_ms": 10_000,
                    "kind": "review",
                    "reason": "short uncertain fragment",
                    "candidate_kind": "unclassified",
                    "candidate_index": 0,
                },
            ],
            "plays": [{"start_ms": 0, "end_ms": 8_000}],
        },
        ui_options={},
        runtime_seconds=0.01,
    )

    assert session.suppress_legacy_unclassified_clips() == 1
    assert session.suppress_legacy_unclassified_clips() == 0
    assert play.enabled is True
    assert fragment.enabled is False
    assert fragment.detection_lineage["suppressed_unclassified"] is True
    assert detector.REVIEW_TAG not in fragment.tags
    queue = session.autodetect_coverage_review_queue()
    assert [item["item_kind"] for item in queue["items"]] == [
        "possible_missed"]

    replacement = Clip(
        start_ms=8_000,
        end_ms=10_000,
        clip_title="[Review] Possible missed",
    )
    restored = session.add_possible_missed_review_clip(1, replacement)
    assert restored.id == fragment.id
    assert len(session.clips) == 2
    assert restored.enabled is True
    assert "suppressed_unclassified" not in restored.detection_lineage
    assert session.autodetect_coverage_review_queue()["items"][0][
        "status"] == "clip_created"
    session.close()


def test_reclaim_fragment_is_safe_and_tracks_undo_redo(tmp_path) -> None:
    session, first, following = _reclaim_session(tmp_path)
    option = session.fragment_reclaim_options(clip_id=first.id)[0]

    assert option.edge == "end"
    assert option.original_boundary_ms == 10_000
    assert option.target_boundary_ms == following.start_ms == 20_000
    assert option.reclaimed_ms == 10_000

    session.reclaim_fragment_into_clip(
        option.segment_index, option.clip_id, option.edge)
    assert session.get_clip(first.id).end_ms == 20_000
    assert session.latest_autodetect_coverage()[1][
        "review_status"] == "clip_created"

    assert session.undo() == "reclaim preserved footage"
    assert session.get_clip(first.id).end_ms == 10_000
    assert session.latest_autodetect_coverage()[1][
        "review_status"] == "pending"

    assert session.redo() == "reclaim preserved footage"
    assert session.get_clip(first.id).end_ms == 20_000
    assert session.latest_autodetect_coverage()[1][
        "review_status"] == "clip_created"
    session.close()


def test_reclaim_can_extend_the_start_of_the_following_play(tmp_path) -> None:
    session, first, following = _reclaim_session(tmp_path)
    option = next(
        item for item in session.fragment_reclaim_options(
            clip_id=following.id)
        if item.edge == "start"
    )

    session.reclaim_fragment_into_clip(
        option.segment_index, option.clip_id, option.edge)

    assert session.get_clip(following.id).start_ms == first.end_ms == 10_000
    assert session.latest_autodetect_coverage()[1][
        "review_status"] == "clip_created"
    session.close()


def test_reclaim_clamps_at_a_play_that_starts_inside_the_fragment(
        tmp_path) -> None:
    session, first, following = _reclaim_session(
        tmp_path, next_start_ms=18_000)
    option = next(
        item for item in session.fragment_reclaim_options(clip_id=first.id)
        if item.edge == "end"
    )

    assert option.target_boundary_ms == 18_000
    assert option.blocker_clip_id == following.id
    session.reclaim_fragment_into_clip(
        option.segment_index, option.clip_id, option.edge)

    assert session.get_clip(first.id).end_ms == following.start_ms
    assert session.get_clip(first.id).end_ms <= following.start_ms
    # Together, the two real plays account for the complete fragment.
    assert session.latest_autodetect_coverage()[1][
        "review_status"] == "clip_created"
    session.close()


def test_ignored_detector_debris_does_not_block_reclaim(tmp_path) -> None:
    session, first, _following = _reclaim_session(tmp_path)
    ignored = session.add_clip(Clip(
        start_ms=12_000,
        end_ms=14_000,
        clip_title="Ignored detector fragment",
        enabled=False,
        detection_lineage={
            "suppressed_unclassified": True,
        },
    ))
    option = next(
        item for item in session.fragment_reclaim_options(clip_id=first.id)
        if item.edge == "end"
    )

    assert option.target_boundary_ms == 20_000
    assert option.blocker_clip_id != ignored.id
    session.close()


def test_resolved_fragment_offers_no_second_reclaim_action(tmp_path) -> None:
    session, first, _following = _reclaim_session(tmp_path)
    option = session.fragment_reclaim_options(clip_id=first.id)[0]
    session.reclaim_fragment_into_clip(
        option.segment_index, option.clip_id, option.edge)

    assert session.fragment_reclaim_options(clip_id=first.id) == []
    session.close()


def test_latest_review_queue_links_an_uncertain_reused_range(tmp_path) -> None:
    source_path = tmp_path / "rerun.mp4"
    source_path.touch()
    source_stat = source_path.stat()
    session = ProjectSession.create(
        "Coverage Rerun", tmp_path, tmp_path / "exports")
    session.project.source_video_path = str(source_path)
    session.project.source_duration_ms = 10_000
    coverage = [{
        "start_ms": 0,
        "end_ms": 10_000,
        "kind": "review",
        "reason": "ambiguous transition",
        "candidate_kind": "play",
        "candidate_index": 0,
    }]
    candidate = {
        "candidate_kind": "play",
        "candidate_index": 0,
        "detector_start_ms": 0,
        "detector_end_ms": 10_000,
        "created_start_ms": 0,
        "created_end_ms": 10_000,
        "angle_starts_ms": [0, 5_000],
        "angle_count": 2,
        "needs_review": True,
        "review_reason": "ambiguous transition",
    }

    def capture(clip: Clip) -> str:
        return session.add_detected_clips(
            [clip],
            [candidate.copy()],
            detector_id="tapesift-play-detect",
            detector_version="coverage-test",
            app_version="coverage-test",
            source={
                "source": {
                    "path": str(source_path),
                    "size_bytes": source_stat.st_size,
                    "mtime_ns": source_stat.st_mtime_ns,
                },
                "duration_ms": 10_000,
            },
            parameters={},
            result={
                "schema_version": "1.0",
                "coverage_schema_version": "1.0",
                "coverage": coverage,
                "plays": [{
                    "start_ms": 0,
                    "end_ms": 10_000,
                    "needs_review": True,
                    "review_reason": "ambiguous transition",
                }],
            },
            ui_options={},
            runtime_seconds=0.01,
        )

    original = Clip(start_ms=0, end_ms=10_000, clip_title="Detected")
    capture(original)
    latest_session_id = capture(
        Clip(start_ms=0, end_ms=10_000, clip_title="Detected again"))

    assert len(session.clips) == 1
    assert session.last_detection_admission()["reused_clip_ids"] == [
        original.id]
    queue = session.autodetect_coverage_review_queue()
    assert queue is not None
    assert queue["session_id"] == latest_session_id
    assert queue["items"][0]["clip_id"] == original.id
    assert queue["items"][0]["status"] == "pending"

    assert session.mark_detection_reviewed([original.id]) == 1
    queue = session.autodetect_coverage_review_queue()
    assert queue is not None
    assert queue["items"][0]["status"] == "reviewed"
    session.close()


def test_latest_coverage_restores_after_project_reopen(tmp_path) -> None:
    source_path = tmp_path / "game.mp4"
    source_path.touch()
    source_stat = source_path.stat()
    session = ProjectSession.create(
        "Coverage Restore", tmp_path, tmp_path / "exports")
    session.project.source_video_path = str(source_path)
    session.project.source_duration_ms = 10_000
    detected = Clip(start_ms=0, end_ms=8_000, clip_title="Detected")
    coverage = [
        {
            "start_ms": 0,
            "end_ms": 8_000,
            "kind": "play",
            "reason": "",
            "candidate_kind": "play",
            "candidate_index": 0,
        },
        {
            "start_ms": 8_000,
            "end_ms": 10_000,
            "kind": "possible_missed",
            "reason": "not included in a detected or review candidate",
            "candidate_kind": "",
            "candidate_index": None,
        },
    ]
    session.add_detected_clips(
        [detected],
        [{
            "candidate_kind": "play",
            "candidate_index": 0,
            "detector_start_ms": 0,
            "detector_end_ms": 8_000,
            "created_start_ms": 0,
            "created_end_ms": 8_000,
            "angle_starts_ms": [0],
            "angle_count": 1,
            "needs_review": True,
            "review_reason": "ambiguous transition",
        }],
        detector_id="tapesift-play-detect",
        detector_version="coverage-test",
        app_version="coverage-test",
        source={
            "source": {
                "path": str(source_path),
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
            },
            "duration_ms": 10_000,
        },
        parameters={},
        result={
            "schema_version": "1.0",
            "coverage_schema_version": "1.0",
            "coverage": coverage,
            "plays": [{
                "start_ms": 0,
                "end_ms": 8_000,
                "needs_review": True,
                "review_reason": "ambiguous transition",
            }],
        },
        ui_options={},
        runtime_seconds=0.01,
    )
    annotated = session.latest_autodetect_coverage()
    assert [
        {
            key: segment[key]
            for key in (
                "start_ms", "end_ms", "kind", "reason",
                "candidate_kind", "candidate_index",
            )
        }
        for segment in annotated
    ] == coverage
    assert annotated[0]["segment_index"] == 0
    assert annotated[0]["review_status"] == ""
    assert annotated[1]["segment_index"] == 1
    assert annotated[1]["review_status"] == "pending"

    queue = session.autodetect_coverage_review_queue()
    assert queue is not None
    assert queue["summary"] == {
        "detected_plays": 1,
        "needs_review": 1,
        "possible_missed": 1,
        "possible_missed_ms": 2_000,
        "pending": 2,
        "resolved": 0,
        "check_first": 0,
        "review_priority": 0,
        "low_signal": 1,
        "audit_version": "1.0",
        "separators": 0,
    }
    assert [item["item_kind"] for item in queue["items"]] == [
        "candidate", "possible_missed"]
    assert queue["items"][0]["attention_label"] == "REQUIRED"
    assert queue["items"][1]["attention_label"] == "LOW SIGNAL"

    assert session.mark_detection_reviewed([detected.id]) == 1
    assert session.dismiss_possible_missed(1) is True
    queue = session.autodetect_coverage_review_queue()
    assert queue is not None
    assert [item["status"] for item in queue["items"]] == [
        "reviewed", "dismissed"]
    assert queue["summary"]["pending"] == 0
    project_path = session.db_path
    session.close()

    reopened = ProjectSession.open(project_path)
    try:
        queue = reopened.autodetect_coverage_review_queue()
        assert queue is not None
        assert [item["status"] for item in queue["items"]] == [
            "reviewed", "dismissed"]

        assert reopened.restore_possible_missed(1) is True
        assert reopened.latest_autodetect_coverage()[1][
            "review_status"] == "pending"

        recovered = Clip(
            start_ms=8_000,
            end_ms=10_000,
            clip_title="[Review] Possible missed",
        )
        reopened.add_possible_missed_review_clip(1, recovered)
        queue = reopened.autodetect_coverage_review_queue()
        assert queue is not None
        assert queue["items"][1]["status"] == "clip_created"
        assert queue["items"][1]["clip_id"] == recovered.id

        assert reopened.undo() == "add possible-missed review clip"
        assert reopened.get_clip(recovered.id) is None
        assert reopened.latest_autodetect_coverage()[1][
            "review_status"] == "pending"

        assert reopened.redo() == "add possible-missed review clip"
        assert reopened.get_clip(recovered.id) is not None
        assert reopened.latest_autodetect_coverage()[1][
            "review_status"] == "clip_created"
    finally:
        reopened.close()
