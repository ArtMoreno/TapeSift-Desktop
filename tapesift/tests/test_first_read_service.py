"""First Read suggests; only the analyst decides.

The tests that matter here are the ones protecting that boundary. A
suggestion that reaches ``run_pass`` becomes indistinguishable from analyst
truth and poisons every dataset exported afterwards.
"""

from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess

import pytest

from tapesift.services import first_read_service as fr
from tapesift.services.run_pass_label_service import resolve_run_pass_label

FFMPEG = fr.ffmpeg_service.find_executable("ffmpeg", os.environ.get("TAPESIFT_TEST_FFMPEG", ""))


@pytest.fixture
def rendered(monkeypatch, tmp_path):
    """Stand in for FFmpeg so the tests exercise the decision, not the codec."""
    def fake(ffmpeg_path, source, start_ms, end_ms, out_path, **view):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"jpeg-bytes")
        return True
    monkeypatch.setattr(fr, "build_contact_sheet", fake)
    return tmp_path


def test_agreeing_views_produce_a_suggestion(rendered):
    read = fr.read_play(
        "ffmpeg", Path("film.mp4"), 0, 10_000, rendered,
        api_key="k", asker=lambda image: "RUN", now="t")
    assert read.label == "run"
    assert read.agreement is True
    assert read.needs_analyst is False


def test_disagreeing_views_refuse_to_suggest(rendered):
    """Both views are near chance when they differ, so neither is offered."""
    replies = iter(["RUN", "PASS"])
    read = fr.read_play(
        "ffmpeg", Path("film.mp4"), 0, 10_000, rendered,
        api_key="k", asker=lambda image: next(replies), now="t")
    assert read.label is None
    assert read.agreement is False
    assert read.needs_analyst is True
    # The individual answers are kept: they explain the refusal.
    assert read.views == ("run", "pass")


def test_a_suggestion_never_becomes_an_analyst_label(rendered):
    """The invariant the whole design rests on."""
    read = fr.read_play(
        "ffmpeg", Path("film.mp4"), 0, 10_000, rendered,
        api_key="k", asker=lambda image: "RUN", now="t")
    stored = json.loads(fr.store("", read))

    # It lives in analysis, under its own key, and writes no label field.
    assert fr.ANALYSIS_KEY in stored
    assert "run_pass" not in stored
    assert "run_pass" not in stored[fr.ANALYSIS_KEY]

    # A clip carrying only a First Read has no trainable label at all.
    resolution = resolve_run_pass_label({}, ())
    assert resolution.trainable is False


def test_store_preserves_other_analysis_data():
    existing = json.dumps({"snap_onset_ms": 1234})
    read = fr.FirstRead(label="pass", agreement=True, views=("pass", "pass"))
    stored = json.loads(fr.store(existing, read))
    assert stored["snap_onset_ms"] == 1234
    assert stored[fr.ANALYSIS_KEY]["label"] == "pass"


def test_store_survives_corrupt_analysis_json():
    read = fr.FirstRead(label="run", agreement=True)
    stored = json.loads(fr.store("{not json", read))
    assert stored[fr.ANALYSIS_KEY]["label"] == "run"


def test_load_round_trips_a_suggestion():
    read = fr.FirstRead(
        label="run", agreement=True, views=("run", "run"), created_at="t")
    back = fr.load(fr.store("", read))
    assert back is not None
    assert back.label == "run"
    assert back.agreement is True
    assert back.views == ("run", "run")


def test_load_returns_none_without_a_suggestion():
    assert fr.load("") is None
    assert fr.load("{}") is None
    assert fr.load("[]") is None
    assert fr.load("garbage") is None


@pytest.mark.parametrize("reply,expected", [
    ("RUN", "run"),
    ("PASS", "pass"),
    ("  run\n", "run"),
    ("This is a running play", "run"),
    # Naming both words is ambiguous, not a vote for whichever came first.
    ("not a run, it is a pass", None),
    ("RUN or PASS", None),
    ("", None),
    ("I cannot tell", None),
])
def test_parse_answer_refuses_ambiguity(reply, expected):
    assert fr.parse_answer(reply) == expected


def test_missing_api_key_is_a_clear_error():
    with pytest.raises(fr.FirstReadError) as excinfo:
        fr.ask_model(b"jpeg", api_key="")
    assert "Settings" in str(excinfo.value)


def test_unrenderable_play_is_reported_not_guessed(monkeypatch):
    monkeypatch.setattr(fr, "build_contact_sheet",
                        lambda *a, **k: False)
    read = fr.read_play(
        "ffmpeg", Path("film.mp4"), 0, 10_000, Path("."),
        api_key="k", asker=lambda image: "RUN")
    assert read.label is None
    assert read.needs_analyst is True
    assert "render" in read.error


@pytest.mark.parametrize("returncode,fresh_output", [(1, b""), (0, b""), (1, b"partial")])
def test_failed_render_never_sends_previous_play(monkeypatch, tmp_path, returncode, fresh_output):
    for view in ("a", "b"):
        (tmp_path / f"first-read-{view}.jpg").write_bytes(b"previous-play")

    def failed_render(command, **kwargs):
        if fresh_output:
            Path(command[-1]).write_bytes(fresh_output)
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(fr.subprocess, "run", failed_render)
    calls = []
    read = fr.read_play(
        "ffmpeg", Path("missing.mp4"), 0, 10_000, tmp_path,
        api_key="unused", asker=lambda image: calls.append(image) or "RUN")
    assert calls == []
    assert read.needs_analyst and read.label is None
    assert "render" in read.error


@pytest.mark.skipif(not FFMPEG, reason="needs an ffmpeg binary")
@pytest.mark.parametrize("failure", ["missing_source", "past_end"])
def test_real_ffmpeg_failure_cannot_reuse_successful_play(tmp_path, failure):
    ffmpeg = FFMPEG
    source = tmp_path / "synthetic.mp4"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=160x90:rate=8:duration=4", "-c:v", "mpeg4", str(source),
    ], check=True, capture_output=True)
    calls = []
    ask = lambda image: calls.append(image) or "RUN"
    first = fr.read_play(ffmpeg, source, 0, 4000, tmp_path, api_key="unused", asker=ask)
    assert first.label == "run" and len(calls) == 2
    bad_source = tmp_path / "missing.mp4" if failure == "missing_source" else source
    start_ms = 0 if failure == "missing_source" else 40_000
    second = fr.read_play(ffmpeg, bad_source, start_ms, start_ms + 4000, tmp_path,
                          api_key="unused", asker=ask)
    assert second.needs_analyst and second.label is None
    assert len(calls) == 2


@pytest.mark.parametrize("stage", ["unlink", "launch"])
def test_render_os_error_is_an_honest_failure(monkeypatch, tmp_path, stage):
    def unavailable(*args, **kwargs):
        raise OSError("locked file or missing executable")

    if stage == "unlink":
        monkeypatch.setattr(Path, "unlink", unavailable)
    else:
        monkeypatch.setattr(fr.subprocess, "run", unavailable)
    calls = []
    result = fr.read_play("missing-ffmpeg", Path("film.mp4"), 0, 4000, tmp_path,
                          api_key="unused", asker=lambda image: calls.append(image) or "RUN")
    assert result.needs_analyst and result.error
    assert calls == []


def test_the_key_is_never_written_into_the_payload(rendered):
    read = fr.read_play(
        "ffmpeg", Path("film.mp4"), 0, 10_000, rendered,
        api_key="sk-secret-value", asker=lambda image: "RUN")
    assert "sk-secret-value" not in json.dumps(read.to_payload())
    assert "sk-secret-value" not in fr.store("", read)


def test_view_signatures_record_how_the_sheets_were_sampled():
    payload = fr.FirstRead(label="run", agreement=True).to_payload()
    assert payload["frames_a"] == "4x2@960px/first50%"
    assert payload["frames_b"] == "4x4@960px/first50%"


def test_qwen_request_uses_tested_route_and_accepts_verified_response():
    def opener(request, **kwargs):
        body = json.loads(request.data)
        assert body["provider"] == {
            "only": ["alibaba"], "allow_fallbacks": False, "require_parameters": True}
        assert (body["temperature"], body["top_k"], body["seed"], body["max_tokens"]) == (0, 1, 0, 64)
        assert body["messages"][0]["content"][0]["text"] == fr.PROMPT
        assert body["messages"][0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,anBlZw=="
        return io.StringIO(json.dumps({"provider": "Alibaba", "model": fr.DEFAULT_MODEL,
            "choices": [{"message": {"content": "RUN"}}]}))
    assert fr.ask_model(b"jpeg", api_key="unused", opener=opener) == "RUN"


@pytest.mark.parametrize("metadata", [
    {}, {"provider": "Other", "model": fr.DEFAULT_MODEL},
    {"provider": "Alibaba", "model": "other/model"},
])
def test_qwen_rejects_unverified_provider_or_model(metadata):
    response = {**metadata, "choices": [{"message": {"content": "RUN"}}]}
    with pytest.raises(fr.FirstReadError, match="verify"):
        fr.ask_model(b"jpeg", api_key="unused",
            opener=lambda *a, **k: io.StringIO(json.dumps(response)))


@pytest.mark.parametrize("options", [
    {"model": "custom/model"}, {"endpoint": "http://localhost:1234/v1/chat/completions"},
])
def test_custom_engines_keep_their_existing_request_contract(options):
    def opener(request, **kwargs):
        body = json.loads(request.data)
        assert "provider" not in body and "top_k" not in body
        return io.StringIO('{"choices":[{"message":{"content":"PASS"}}]}')
    assert fr.ask_model(b"jpeg", api_key="unused", opener=opener, **options) == "PASS"
