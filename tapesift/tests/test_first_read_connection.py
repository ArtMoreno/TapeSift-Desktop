"""Retry a failed request without replaying a paid view or losing prior plays."""

from __future__ import annotations

import base64
import http.client
import io
import json
from pathlib import Path
import ssl
import urllib.error
import urllib.request

import pytest

from tapesift.models.clip import Clip
from tapesift.services import first_read_batch as batch
from tapesift.services import first_read_service as fr


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("This test must not contact a provider")
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)


def reply():
    return io.StringIO(json.dumps({
        "provider": "Alibaba", "model": fr.DEFAULT_MODEL,
        "choices": [{"message": {"content": "RUN"}}],
    }))


def render(ffmpeg, source, start, end, out, **view):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(f"{start}:{view['rows']}".encode())
    return True


def sent_image(request):
    content = json.loads(request.data)["messages"][0]["content"]
    return base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])


def test_second_view_retry_keeps_first_view_and_saves_each_completed_play(
        monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "build_contact_sheet", render)
    calls, saves, waits = [], [], []
    clips = [Clip(start_ms=0, end_ms=10_000),
             Clip(start_ms=10_000, end_ms=20_000)]

    def open_url(request, **kwargs):
        calls.append(sent_image(request))
        if len(calls) == 2:
            raise urllib.error.URLError(ConnectionResetError("connection reset"))
        if len(calls) == 4:
            assert saves == [clips[0].id]
        return reply()

    def save(cid, analysis):
        saves.append(cid)
        next(c for c in clips if c.id == cid).analysis = json.loads(analysis)

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    options = dict(ffmpeg_path="ffmpeg", source=Path("film.mp4"),
                   scratch_dir=tmp_path, api_key="offline-test-key",
                   save=save, sleep=waits.append)
    summary = batch.run(clips, **options)
    assert summary.suggested == 2 and not summary.stopped_reason
    assert calls == [b"0:2", b"0:4", b"0:4", b"10000:2", b"10000:4"]
    assert waits == [1.0]
    assert saves == [c.id for c in clips]
    assert all(fr.load(c.analysis_json()).label == "run" for c in clips)

    resumed = batch.run(clips, **options)
    assert resumed.skipped == 2 and resumed.total == 0
    assert len(calls) == 5


@pytest.mark.parametrize("failure", [
    urllib.error.URLError(ConnectionResetError("reset")),
    urllib.error.URLError(TimeoutError("timeout")),
    TimeoutError("read timed out"),
    ConnectionResetError("reset while reading"),
    http.client.RemoteDisconnected("remote closed"),
    http.client.IncompleteRead(b"partial", 20),
    ssl.SSLEOFError("unexpected EOF"),
])
def test_transport_failures_retry_the_identical_request(failure):
    calls, waits = [], []

    def opener(request, **kwargs):
        calls.append((request.full_url, request.data, dict(request.headers), kwargs))
        if len(calls) < 3:
            raise failure
        return reply()

    assert fr.ask_model(b"jpeg", api_key="offline-test-key", opener=opener,
                        sleep=waits.append) == "RUN"
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert waits == [1, 2]


def test_read_body_disconnect_retries_and_closes_each_response():
    opened, waits = [], []

    class BrokenBody(io.StringIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b"partial", 20)

    def opener(*args, **kwargs):
        response = BrokenBody() if not opened else reply()
        opened.append(response)
        return response

    assert fr.ask_model(b"jpeg", api_key="offline-test-key", opener=opener,
                        sleep=waits.append) == "RUN"
    assert len(opened) == 2 and all(r.closed for r in opened)
    assert waits == [1]


def test_exhausted_connection_retries_stop_batch_and_keep_prior_play(
        monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "build_contact_sheet", render)
    calls, saves, waits = [], [], []

    def opener(request, **kwargs):
        calls.append(sent_image(request))
        if len(calls) > 2:
            raise urllib.error.URLError(ConnectionResetError("offline"))
        return reply()

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    clips = [Clip(start_ms=n * 10_000, end_ms=(n + 1) * 10_000) for n in range(3)]
    summary = batch.run(
        clips, ffmpeg_path="ffmpeg", source=Path("film.mp4"),
        scratch_dir=tmp_path, api_key="offline-test-key",
        save=lambda cid, analysis: saves.append(cid), sleep=waits.append)
    assert summary.suggested == 1 and "offline" in summary.stopped_reason
    assert saves == [clips[0].id]
    assert calls == [b"0:2", b"0:4", b"10000:2", b"10000:2", b"10000:2"]
    assert waits == [1, 2]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_http_errors_are_not_connection_retries(status):
    calls, waits = [], []

    def opener(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError(
            fr.DEFAULT_ENDPOINT, status, "refused", {"Retry-After": "7"},
            io.BytesIO(b"refused"))

    expected = fr.FirstReadRateLimited if status == 429 else fr.FirstReadError
    with pytest.raises(expected) as exc:
        fr.ask_model(b"jpeg", api_key="offline-test-key", opener=opener,
                     sleep=waits.append)
    assert len(calls) == 1 and waits == []
    if status == 429:
        assert exc.value.retry_after == 7


@pytest.mark.parametrize("hint", ["inf", "-inf", "nan", "-1", "invalid"])
def test_invalid_retry_after_uses_finite_default_wait(monkeypatch, tmp_path, hint):
    monkeypatch.setattr(fr, "build_contact_sheet", render)
    calls, waits = [], []

    def opener(request, **kwargs):
        calls.append(sent_image(request))
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                fr.DEFAULT_ENDPOINT, 429, "slow down", {"Retry-After": hint},
                io.BytesIO(b"slow down"))
        return reply()

    def sleep(seconds):
        waits.append(seconds)
        assert len(waits) <= 3, "Invalid Retry-After must not loop forever"

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    summary = batch.run(
        [Clip(start_ms=0, end_ms=10_000)], ffmpeg_path="ffmpeg",
        source=Path("film.mp4"), scratch_dir=tmp_path, api_key="offline-test-key",
        save=lambda *args: None, should_cancel=lambda: False,
        sleep=sleep, backoff_s=0.2)
    assert summary.suggested == 1 and not summary.stopped_reason
    assert calls == [b"0:2", b"0:2", b"0:4"]
    assert waits == [0.1, 0.1]


@pytest.mark.parametrize("failure", [
    urllib.error.URLError(ssl.SSLCertVerificationError("untrusted certificate")),
    urllib.error.URLError("invalid proxy configuration"),
])
def test_nontransient_url_failures_stop_without_retry(failure):
    calls, waits = [], []

    def opener(*args, **kwargs):
        calls.append(1)
        raise failure

    with pytest.raises(fr.FirstReadError):
        fr.ask_model(b"jpeg", api_key="offline-test-key", opener=opener,
                     sleep=waits.append)
    assert len(calls) == 1 and waits == []


def test_wrong_provider_metadata_is_not_retried():
    calls, waits = [], []

    def opener(*args, **kwargs):
        calls.append(1)
        return io.StringIO(json.dumps({"provider": "Other", "model": fr.DEFAULT_MODEL,
                                     "choices": [{"message": {"content": "RUN"}}]}))

    with pytest.raises(fr.FirstReadError, match="verify"):
        fr.ask_model(b"jpeg", api_key="offline-test-key", opener=opener,
                     sleep=waits.append)
    assert len(calls) == 1 and waits == []


@pytest.mark.parametrize("failure", [
    urllib.error.URLError(ConnectionResetError("reset")),
    urllib.error.HTTPError(fr.DEFAULT_ENDPOINT, 429, "slow down",
                           {"Retry-After": "60"}, io.BytesIO(b"slow down")),
])
def test_stop_during_retry_wait_sends_nothing_more(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(fr, "build_contact_sheet", render)
    calls, saves, waits = [], [], []
    stopped = False

    def opener(request, **kwargs):
        calls.append(sent_image(request))
        if len(calls) > 2:
            raise failure
        return reply()

    def sleep(seconds):
        nonlocal stopped
        waits.append(seconds)
        stopped = True

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    clips = [Clip(start_ms=n * 10_000, end_ms=(n + 1) * 10_000) for n in range(3)]
    summary = batch.run(
        clips, ffmpeg_path="ffmpeg", source=Path("film.mp4"),
        scratch_dir=tmp_path, api_key="offline-test-key",
        save=lambda cid, analysis: saves.append(cid), sleep=sleep,
        should_cancel=lambda: stopped)
    assert summary.cancelled and not summary.stopped_reason
    assert summary.suggested == 1 and saves == [clips[0].id]
    assert calls == [b"0:2", b"0:4", b"10000:2"]
    assert waits == [0.1]


def test_stop_after_first_view_does_not_send_second(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "build_contact_sheet", render)
    calls, saves = [], []
    stopped = False

    def opener(request, **kwargs):
        nonlocal stopped
        calls.append(sent_image(request))
        stopped = True
        return reply()

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    summary = batch.run(
        [Clip(start_ms=0, end_ms=10_000)], ffmpeg_path="ffmpeg",
        source=Path("film.mp4"), scratch_dir=tmp_path, api_key="offline-test-key",
        save=lambda *args: saves.append(args), should_cancel=lambda: stopped)
    assert summary.cancelled and not summary.stopped_reason
    assert calls == [b"0:2"] and saves == []


def test_cancelled_request_never_opens_a_connection():
    with pytest.raises(fr.FirstReadCancelled):
        fr.ask_model(b"jpeg", api_key="offline-test-key", should_cancel=lambda: True)


def test_worker_stop_reaches_request_retry_and_emits_cancelled_summary(
        monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from tapesift.workers.first_read_worker import FirstReadWorker

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(fr, "build_contact_sheet", render)
    calls, saved, finished = [], [], []
    clips = [Clip(start_ms=n * 10_000, end_ms=(n + 1) * 10_000) for n in range(3)]
    worker = FirstReadWorker(
        clips, ffmpeg_path="ffmpeg", source=Path("film.mp4"),
        scratch_dir=tmp_path, api_key="offline-test-key")

    def opener(request, **kwargs):
        calls.append(sent_image(request))
        if len(calls) > 2:
            worker.stop()
            raise urllib.error.URLError(ConnectionResetError("reset"))
        return reply()

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    worker.result_ready.connect(lambda cid, analysis: saved.append(cid))
    worker.finished_batch.connect(finished.append)
    worker.start()
    assert worker.wait(2000)
    app.processEvents()
    assert saved == [clips[0].id]
    assert len(finished) == 1 and finished[0].cancelled
    assert finished[0].suggested == 1 and not finished[0].stopped_reason
    assert calls == [b"0:2", b"0:4", b"10000:2"]
