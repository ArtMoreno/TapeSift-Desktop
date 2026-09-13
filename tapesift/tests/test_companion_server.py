"""The phone view of a project: reading it, and writing tags back."""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

from tapesift.database.migrations import run_migrations
from tapesift.services.companion_server import (
    ALLOWED_RUN_PASS, PAGE, REVIEW_TAG, Play, ProjectView, _parse_range,
    client_is_local, desktop_has_it_open, main, serve, stable_token,
    tailscale_urls)
from tapesift.services import companion_server

STAMP = "2026-08-04T00:00:00+00:00"


def test_roster_suggestions_use_2026_numbers_without_erasing_logged_names():
    from types import SimpleNamespace
    replies = []
    handler = SimpleNamespace(
        project=SimpleNamespace(plays=lambda: [SimpleNamespace(
            details={"player_name": "Carson Beck"})]),
        _send_bytes=lambda data, _mime: replies.append(json.loads(data)),
    )
    companion_server.CompanionHandler._send_roster(handler)
    assert "1 Malachi Toney" in replies[0]["squad"]
    assert "10 Malachi Toney" not in replies[0]["squad"]
    assert not any("Carson Beck" in name for name in replies[0]["squad"])
    assert replies[0]["used"] == ["Carson Beck"]


def test_tailscale_urls_keep_the_write_token(monkeypatch):
    monkeypatch.setattr(
        companion_server, "_tailscale_hosts",
        lambda: ("pc.example.ts.net", "100.90.242.82"))
    assert tailscale_urls(8733, "private-token") == [
        "http://pc.example.ts.net:8733/?t=private-token",
        "http://100.90.242.82:8733/?t=private-token",
    ]


def test_mobile_controls_keep_the_wheel_and_play_boundaries():
    assert 'id="dial"' in PAGE
    assert 'id="tlin"' in PAGE
    assert 'id="tlout"' in PAGE
    assert "IN / Play start" in PAGE
    assert "OUT / Play end" in PAGE


def _rows(path):
    db = sqlite3.connect(path)
    try:
        return db.execute(
            "SELECT clip_number, tags_json, details_json, updated_at"
            " FROM clips ORDER BY clip_number").fetchall()
    finally:
        db.close()


@contextlib.contextmanager
def running(project, *, writable=False, token=""):
    """A companion on a port the OS picks, shut down and closed after.

    Both halves matter. `shutdown()` ends the request loop but leaves the
    socket listening, and HTTPServer sets allow_reuse_address - so on
    Windows a later bind to the same port succeeds, the two listeners
    share it, and a connection handed to the stopped one dies as
    WinError 10053 partway through an unrelated test. Fixed ports made
    that a matter of time; an ephemeral port plus `server_close()` takes
    the whole failure mode away.
    """
    server, url = serve(project, port=0, host="127.0.0.1",
                        writable=writable, token=token)
    token = (parse_qs(urlparse(url).query).get("t") or [""])[0]
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", token
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def schema(tmp_path_factory):
    """An empty database on the real schema, migrated once and copied.

    Running the migrations per test costs about a second each - nine
    scripts, nine commits - which was more than the rest of this file put
    together. A file copy is the same schema for a thousandth of it.
    """
    path = tmp_path_factory.mktemp("schema") / "empty.tapesift"
    db = sqlite3.connect(path)
    run_migrations(db)
    db.close()
    return path


def _blank_project(schema, path):
    shutil.copyfile(schema, path)
    return sqlite3.connect(path)


@pytest.fixture
def project(tmp_path, schema):
    """A project file on the real schema, with two plays and a fake film.

    Built from the real migrations rather than hand-rolled CREATE TABLEs:
    once the companion writes to this file, a convenient subset of the
    columns would be testing the wrong thing. `updated_at` is NOT NULL in
    the real schema and a hand-rolled table would have hidden that.
    """
    film = tmp_path / "game.mp4"
    film.write_bytes(b"x" * 5000)
    path = tmp_path / "p.tapesift"
    db = _blank_project(schema, path)
    db.execute(
        "INSERT INTO projects (id, name, source_video_path, created_at,"
        " updated_at) VALUES (1, 'Pitt O', ?, ?, ?)", (str(film), STAMP, STAMP))
    db.executemany(
        "INSERT INTO clips (id, project_id, clip_number, order_index,"
        " start_ms, end_ms, clip_title, tags_json, details_json, enabled,"
        " created_at, updated_at) VALUES (?,1,?,?,?,?,?,?,?,?,?,?)", [
            ("c1", 1, 0, 500, 4500, "Play 001", '["Pass"]',
             '{"down_distance": "3rd & 8"}', 1, STAMP, STAMP),
            ("c2", 2, 1, 9000, 12000, "Play 002", '["Run"]',
             '{"down_distance": "1st & 10"}', 1, STAMP, STAMP),
            ("c3", 3, 2, 0, 1, "Disabled", "[]", "{}", 0, STAMP, STAMP),
        ])
    db.commit()
    db.close()
    return path


class TestReadingAProject:
    def test_disabled_clips_are_not_served(self, project):
        assert len(ProjectView(project).plays()) == 2

    def test_tags_and_details_come_through(self, project):
        play = ProjectView(project).plays()[0]
        assert play.tags == ["Pass"]
        assert play.details["down_distance"] == "3rd & 8"

    def test_the_film_path_is_read(self, project):
        name, film = ProjectView(project).name_and_film()
        assert name == "Pitt O"
        assert film.is_file()

    def test_a_corrupt_json_column_does_not_break_the_list(self, tmp_path,
                                                           schema):
        path = tmp_path / "bad.tapesift"
        db = _blank_project(schema, path)
        db.execute(
            "INSERT INTO projects (id, name, created_at, updated_at)"
            " VALUES (1, 'x', ?, ?)", (STAMP, STAMP))
        db.execute(
            "INSERT INTO clips (id, project_id, clip_number, start_ms,"
            " end_ms, tags_json, details_json, enabled, created_at,"
            " updated_at) VALUES ('c1',1,1,0,1,'not json','{{',1,?,?)",
            (STAMP, STAMP))
        db.commit()
        db.close()
        play = ProjectView(path).plays()[0]
        assert play.tags == [] and play.details == {}

    def test_the_database_is_opened_read_only(self, project):
        """A phone must never be able to damage a project."""
        view = ProjectView(project)
        with pytest.raises(sqlite3.OperationalError):
            with view._connect() as db:
                db.execute("DELETE FROM clips")


class TestSearchHaystack:
    def test_one_box_searches_title_tags_and_details(self):
        play = Play(1, "Play 007", 0, 1, ["Pass"], {"down_distance": "3rd & 8"})
        hay = play.haystack()
        assert "play 007" in hay and "pass" in hay and "3rd & 8" in hay


class TestRangeRequests:
    """iOS will not play a video unless ranges are answered correctly."""

    @pytest.mark.parametrize("header,expected", [
        ("bytes=0-", (0, 999)),
        ("bytes=100-200", (100, 200)),
        ("bytes=-50", (950, 999)),
        ("bytes=500-99999", (500, 999)),
        ("", None),
        ("nonsense", None),
        ("bytes=5000-", None),
    ])
    def test_ranges_parse(self, header, expected):
        assert _parse_range(header, 1000) == expected


class TestServing:
    @pytest.fixture
    def url(self, project):
        with running(project) as (base, _):
            yield base

    def test_the_api_lists_plays(self, url):
        data = json.load(urllib.request.urlopen(f"{url}/api/plays"))
        assert data["project"] == "Pitt O"
        assert len(data["plays"]) == 2
        assert data["film_available"] is True

    def test_the_page_carries_the_project_name(self, url):
        page = urllib.request.urlopen(url).read().decode()
        assert "Pitt O" in page

    def test_a_range_request_gets_206_and_only_those_bytes(self, url):
        request = urllib.request.Request(
            f"{url}/film", headers={"Range": "bytes=0-99"})
        response = urllib.request.urlopen(request)
        assert response.status == 206
        assert response.headers["Content-Range"] == "bytes 0-99/5000"
        assert len(response.read()) == 100

    def test_an_unknown_route_is_404(self, url):
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{url}/secrets")
        assert caught.value.code == 404


class TestOnlyLocalClientsAreAnswered:
    """The socket listens everywhere; the answer is what is restricted.

    Binding one interface would drop either wifi or the tailnet, so reach
    is limited per request instead. Without this the companion served the
    whole project list and streamed the film to anything that could route
    to the port - and the read routes carry no token to fall back on.
    """

    @pytest.mark.parametrize("address", [
        "127.0.0.1", "::1",
        "192.168.1.40", "10.0.0.7", "172.16.5.1",   # private LAN
        "169.254.10.10",                            # link-local
        "100.90.242.82",                            # Tailscale / CGNAT
        "fd7a:115c:a1e0::1",                        # unique local
        "::ffff:192.168.1.40",                      # v4 through a v6 socket
    ])
    def test_local_addresses_are_answered(self, address):
        assert client_is_local(address) is True

    @pytest.mark.parametrize("address", [
        "8.8.8.8", "1.1.1.1",
        "203.0.113.9",              # TEST-NET-3, a routable-shaped address
        "172.32.0.1",               # just outside the private /12
        "100.128.0.1",              # just outside the CGNAT /10
        "2606:4700:4700::1111",     # public v6
        "", "not-an-address", "192.168.1.40; DROP",
    ])
    def test_everything_else_is_refused(self, address):
        assert client_is_local(address) is False

    def test_a_refused_client_gets_403_and_no_project_data(
            self, project, monkeypatch):
        monkeypatch.setattr(companion_server, "client_is_local",
                            lambda address: False)
        with running(project) as (base, _):
            for route in ("/", "/api/projects", "/api/plays", "/film",
                          "/clip/1.mp4", "/reel?plays=1"):
                with pytest.raises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(f"{base}{route}")
                assert caught.value.code == 403, route
                assert b"Pitt O" not in caught.value.read()

    def test_a_refused_client_cannot_write_either(self, project, monkeypatch):
        monkeypatch.setattr(companion_server, "client_is_local",
                            lambda address: False)
        with running(project, writable=True) as (base, token):
            with pytest.raises(urllib.error.HTTPError) as caught:
                _post(base, 1, {"run_pass": "Run"}, token)
            assert caught.value.code == 403


class TestClipExport:
    """Cutting one play out so it can be saved or sent to somebody."""

    def test_the_download_name_carries_the_situation(self):
        from tapesift.services.companion_server import CompanionHandler
        play = Play(7, "Play 007", 0, 4000, ["Pass"],
                    {"quarter": "Q3", "down_distance": "3rd & 8",
                     "run_pass": "Pass"})
        name = CompanionHandler._download_name(play)
        assert name.startswith("007")
        assert name.endswith(".mp4")
        assert "3rd" in name and "Q3" in name

    def test_the_cut_is_keyed_on_the_range_so_an_edit_recuts(self, project,
                                                             tmp_path):
        from tapesift.services.companion_server import CompanionHandler
        handler = CompanionHandler.__new__(CompanionHandler)
        film = tmp_path / "game.mp4"
        first = handler._cut_path(film, Play(1, "t", 0, 4000))
        moved = handler._cut_path(film, Play(1, "t", 500, 4000))
        assert first != moved

    def test_a_play_number_that_does_not_exist_is_404(self, project):
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/clip/404.mp4")
            assert caught.value.code == 404

    def test_a_nonsense_clip_route_is_404(self, project):
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/clip/abc.mp4")
            assert caught.value.code == 404


# --------------------------------------------------------------- writing


def _post(base, number, payload, token="", *, in_query=False):
    target = f"{base}/api/play/{number}"
    headers = {"Content-Type": "application/json"}
    if token and in_query:
        target += f"?t={token}"
    elif token:
        headers["X-TapeSift-Token"] = token
    return urllib.request.urlopen(urllib.request.Request(
        target, data=json.dumps(payload).encode(), headers=headers,
        method="POST"))


class TestWritesAreOffUnlessAskedFor:
    """The read-only guarantee has to survive the arrival of writes."""

    def test_a_view_does_not_write_by_default(self, project):
        with pytest.raises(PermissionError):
            ProjectView(project).update_play(1, run_pass="Run")

    def test_the_default_view_still_opens_the_database_read_only(self, project):
        view = ProjectView(project)
        with pytest.raises(sqlite3.OperationalError):
            with view._connect() as db:
                db.execute("DELETE FROM clips")

    def test_a_read_only_server_refuses_a_post(self, project):
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                _post(base, 1, {"needs_review": True})
            assert caught.value.code == 405
        assert _rows(project)[0][1] == '["Pass"]'

    def test_a_read_only_server_hands_out_a_url_with_no_token(self, project):
        with running(project) as (_, token):
            assert token == ""


class TestWritingTagsBack:
    @pytest.fixture
    def view(self, project):
        return ProjectView(project, writable=True)

    def test_run_pass_lands_in_details(self, view, project):
        play = view.update_play(1, run_pass="Run")
        assert play.details["run_pass"] == "Run"
        assert json.loads(_rows(project)[0][2])["run_pass"] == "Run"

    def test_an_empty_value_clears_the_key_rather_than_storing_blank(
            self, view, project):
        view.update_play(1, run_pass="Run")
        play = view.update_play(1, run_pass="")
        assert "run_pass" not in play.details
        assert "run_pass" not in json.loads(_rows(project)[0][2])

    def test_the_review_flag_goes_on_and_comes_off(self, view, project):
        assert REVIEW_TAG in view.update_play(1, needs_review=True).tags
        assert REVIEW_TAG not in view.update_play(1, needs_review=False).tags

    def test_setting_review_twice_does_not_duplicate_the_tag(self, view):
        view.update_play(1, needs_review=True)
        tags = view.update_play(1, needs_review=True).tags
        assert tags.count(REVIEW_TAG) == 1

    def test_clearing_review_leaves_the_other_tags_alone(self, view):
        view.update_play(1, needs_review=True)
        assert view.update_play(1, needs_review=False).tags == ["Pass"]

    def test_a_field_left_unset_is_not_touched(self, view):
        """Toggling the flag must not blank a run_pass it never sent."""
        view.update_play(1, run_pass="Pass")
        play = view.update_play(1, needs_review=True)
        assert play.details["run_pass"] == "Pass"
        assert play.details["down_distance"] == "3rd & 8"

    def test_one_play_is_written_without_disturbing_its_neighbour(
            self, view, project):
        before = _rows(project)[1]
        view.update_play(1, run_pass="Run", needs_review=True)
        assert _rows(project)[1] == before

    def test_an_unlisted_run_pass_value_is_refused(self, view, project):
        with pytest.raises(ValueError):
            view.update_play(1, run_pass="Wheel Route Left")
        assert json.loads(_rows(project)[0][2]) == {"down_distance": "3rd & 8"}

    def test_an_unknown_play_number_writes_nothing(self, view, project):
        before = _rows(project)
        assert view.update_play(404, run_pass="Run") is None
        assert _rows(project) == before

    def test_a_disabled_clip_cannot_be_written(self, view, project):
        """It is not on the phone's list, so it is not the phone's to edit."""
        before = _rows(project)
        assert view.update_play(3, needs_review=True) is None
        assert _rows(project) == before

    def test_updated_at_moves_so_the_desktop_can_see_the_change(
            self, view, project):
        view.update_play(1, run_pass="Run")
        assert _rows(project)[0][3] != STAMP

    def test_the_desktop_reads_back_what_the_phone_wrote(self, view, project):
        """The real deserialiser, not this test's json.loads."""
        from tapesift.database.connection import open_project_db
        from tapesift.database.repositories import ClipRepository

        view.update_play(1, run_pass="Screen", needs_review=True)
        conn = open_project_db(project)
        try:
            clip = next(c for c in ClipRepository(conn).list_for_project(1)
                        if c.clip_number == 1)
        finally:
            conn.close()
        assert clip.details["run_pass"] == "Screen"
        assert REVIEW_TAG in clip.tags


class TestTheReviewTagHasNotDrifted:
    def test_it_is_the_tag_the_detector_writes(self):
        """This server keeps its own copy so it can run standalone."""
        from tapesift.services import play_detect_service
        assert REVIEW_TAG == play_detect_service.REVIEW_TAG


class TestWriteAccess:
    """Nobody authenticates. A token is the floor, and it has to hold."""

    @pytest.fixture
    def live(self, project):
        with running(project, writable=True) as pair:
            yield pair

    def test_a_write_with_no_token_is_refused(self, live, project):
        base, _ = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, 1, {"needs_review": True})
        assert caught.value.code == 403
        assert _rows(project)[0][1] == '["Pass"]'

    def test_a_wrong_token_is_refused(self, live, project):
        base, token = live
        # Built to differ rather than edited to differ: substituting a
        # fixed character (token[:-1] + "x") hands back the real token
        # whenever it already ended in that character. That is one run in
        # sixty-four, and it reads as a mysterious flake in CI.
        wrong = ("a" if token[0] != "a" else "b") + token[1:]
        assert wrong != token
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, 1, {"needs_review": True}, wrong)
        assert caught.value.code == 403
        assert _rows(project)[0][1] == '["Pass"]'

    def test_the_token_works_in_a_header(self, live, project):
        base, token = live
        body = json.load(_post(base, 1, {"run_pass": "Run"}, token))
        assert body["details"]["run_pass"] == "Run"

    def test_the_token_works_in_the_query_so_the_url_alone_is_enough(
            self, live, project):
        base, token = live
        body = json.load(
            _post(base, 1, {"needs_review": True}, token, in_query=True))
        assert REVIEW_TAG in body["tags"]

    def test_reading_never_needs_the_token(self, live):
        base, _ = live
        data = json.load(urllib.request.urlopen(f"{base}/api/plays"))
        assert len(data["plays"]) == 2

    def test_a_reader_without_the_token_is_told_it_cannot_write(self, live):
        base, _ = live
        data = json.load(urllib.request.urlopen(f"{base}/api/plays"))
        assert data["writable"] is False

    def test_a_reader_with_the_token_is_told_it_can(self, live):
        base, token = live
        data = json.load(urllib.request.urlopen(urllib.request.Request(
            f"{base}/api/plays", headers={"X-TapeSift-Token": token})))
        assert data["writable"] is True


class TestTheReel:
    """Several plays cut into one file.

    Saving a cut-up one play at a time was the phone's worst job: a
    browser cannot be handed forty downloads and nobody can share forty
    files. These cover the request surface; the FFmpeg join itself needs
    real film and is exercised by hand.
    """

    def test_it_asks_for_at_least_one_play(self, project):
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/reel")
            assert caught.value.code == 400

    def test_a_non_numeric_play_is_refused(self, project):
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/reel?plays=1,banana")
            assert caught.value.code == 400

    def test_an_unknown_play_number_is_404(self, project):
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/reel?plays=1,404")
            assert caught.value.code == 404

    def test_a_disabled_clip_cannot_be_reeled(self, project):
        """Clip 3 is disabled, so it is not on the list and not exportable."""
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/reel?plays=3")
            assert caught.value.code == 404

    def test_too_many_plays_is_refused_rather_than_queued(self, project):
        from tapesift.services.companion_server import MAX_REEL_PLAYS
        many = ",".join(str(n) for n in range(MAX_REEL_PLAYS + 5))
        with running(project) as (base, _):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/reel?plays={many}")
            assert caught.value.code == 413

    def test_the_cache_key_covers_every_range_in_the_reel(self, tmp_path):
        """Two plays in, two plays with one nudged, must not collide."""
        from tapesift.services.companion_server import CompanionHandler
        handler = CompanionHandler.__new__(CompanionHandler)
        film = tmp_path / "game.mp4"
        film.write_bytes(b"x" * 100)
        a = [Play(1, "t", 0, 4000), Play(2, "t", 9000, 12000)]
        b = [Play(1, "t", 0, 4000), Play(2, "t", 9500, 12000)]
        assert handler._reel_path(film, a) != handler._reel_path(film, b)

    def test_order_does_not_change_the_cache_key(self, tmp_path):
        """The route sorts by film time, so the same set is the same reel."""
        from tapesift.services.companion_server import CompanionHandler
        handler = CompanionHandler.__new__(CompanionHandler)
        film = tmp_path / "game.mp4"
        film.write_bytes(b"x" * 100)
        one = Play(1, "t", 0, 4000)
        two = Play(2, "t", 9000, 12000)
        assert (handler._reel_path(film, [one, two])
                == handler._reel_path(film, [one, two]))
        assert (handler._reel_path(film, [two, one])
                != handler._reel_path(film, [one, two]))


class TestTheTokenSurvivesARestart:
    """A phone keeps a bookmark. A token that changed on every restart
    left it loading fine and quietly unable to write, which reads as the
    whole thing being broken rather than as an expired credential.
    """

    def test_the_same_token_comes_back(self, tmp_path):
        path = tmp_path / "companion-token"
        first = stable_token(path)
        assert first and stable_token(path) == first

    def test_it_is_written_where_it_can_be_found(self, tmp_path):
        path = tmp_path / "companion-token"
        token = stable_token(path)
        assert path.read_text().strip() == token

    def test_rotating_replaces_it_for_good(self, tmp_path):
        path = tmp_path / "companion-token"
        first = stable_token(path)
        second = stable_token(path, rotate=True)
        assert second != first
        assert stable_token(path) == second

    def test_an_unwritable_location_still_yields_a_working_token(self, tmp_path):
        """Losing persistence must not lose the run."""
        blocked = tmp_path / "nope"
        blocked.write_text("this is a file, not a directory")
        assert len(stable_token(blocked / "companion-token")) > 8

    def test_a_supplied_token_is_the_one_served(self, project):
        with running(project, writable=True, token="chosen-token") as (base, tok):
            assert tok == "chosen-token"
            body = json.load(_post(base, 1, {"run_pass": "Run"}, tok))
            assert body["details"]["run_pass"] == "Run"

    def test_a_read_only_server_ignores_a_supplied_token(self, project):
        with running(project, token="chosen-token") as (_, tok):
            assert tok == ""


class TestTheWriteSurfaceIsTheWholeScope:
    """v1 is Run/Pass and the review flag. The server enforces that."""

    @pytest.fixture
    def live(self, project):
        with running(project, writable=True) as pair:
            yield pair

    def test_a_key_outside_the_scope_is_refused(self, live, project):
        base, token = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, 1, {"notes": "big hit"}, token)
        assert caught.value.code == 400
        assert json.loads(_rows(project)[0][2]) == {"down_distance": "3rd & 8"}

    def test_a_bad_run_pass_value_is_refused_over_http(self, live, project):
        base, token = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, 1, {"run_pass": "Trick"}, token)
        assert caught.value.code == 400

    def test_a_non_boolean_review_flag_is_refused(self, live):
        base, token = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, 1, {"needs_review": "yes"}, token)
        assert caught.value.code == 400

    def test_an_unknown_play_number_is_404(self, live):
        base, token = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, 404, {"needs_review": True}, token)
        assert caught.value.code == 404

    def test_a_refusal_says_why_in_json_a_phone_can_show(self, live):
        base, token = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, 1, {"run_pass": "Trick"}, token)
        assert "run_pass" in json.load(caught.value)["error"]

    def test_every_offered_value_is_accepted(self, live):
        base, token = live
        for value in ALLOWED_RUN_PASS:
            body = json.load(_post(base, 1, {"run_pass": value}, token))
            assert body["details"].get("run_pass", "") == value


class _Stalling:
    """A write connection that pauses between reading a row and writing it.

    That gap is exactly where a lost update lives, so holding it open
    turns a rare interleaving into a certain one. `update_play` uses only
    execute and close, so a proxy is enough and the real pragmas and
    timeouts stay in place.
    """

    def __init__(self, db, opened, hold):
        self._db, self._opened, self._hold = db, opened, hold

    def execute(self, sql, *args):
        cursor = self._db.execute(sql, *args)
        if sql.startswith("SELECT rowid"):
            self._opened.set()
            time.sleep(self._hold)
        return cursor

    def close(self):
        self._db.close()


class TestTwoWritersAtOnce:
    """Two phones, one play.

    Both fields live inside JSON columns, so every edit is a
    read-modify-write of the whole column. With the read and the write in
    separate statements, the second writer reads the pre-edit JSON and
    its UPDATE silently undoes the first. Written to fail if the
    transaction in `update_play` is removed - a version without it was
    run against this test to check that it does.
    """

    def test_an_edit_landing_mid_read_modify_write_is_not_erased(
            self, project):
        slow = ProjectView(project, writable=True)
        opened = threading.Event()
        slow._connect_write = (  # noqa: SLF001 - the seam is the point
            lambda _real=slow._connect_write:
            _Stalling(_real(), opened, hold=0.4))
        fast = ProjectView(project, writable=True)
        failures = []

        def run(work):
            try:
                work()
            except Exception as exc:  # noqa: BLE001 - reported, not hidden
                failures.append(exc)

        first = threading.Thread(
            target=run, args=(lambda: slow.update_play(1, run_pass="Run"),))
        first.start()
        assert opened.wait(5), "the stalling writer never reached its read"

        # Squarely inside the other writer's read-to-write window.
        second = threading.Thread(
            target=run, args=(lambda: fast.update_play(1, needs_review=True),))
        second.start()
        for thread in (first, second):
            thread.join(20)

        assert not failures, failures
        play = next(p for p in ProjectView(project).plays() if p.number == 1)
        assert play.details["run_pass"] == "Run"
        assert REVIEW_TAG in play.tags


class TestTheDesktopMustBeClosed:
    """The one hazard SQLite cannot be configured out of.

    `ProjectSession` holds every clip in memory and saves by deleting all
    of them and re-inserting from memory, so an open desktop app will
    erase a phone's edit at its next save with no error anywhere.
    """

    def test_an_open_connection_is_visible_from_outside(self, project):
        assert desktop_has_it_open(project) is False
        from tapesift.database.connection import open_project_db
        conn = open_project_db(project)
        try:
            assert desktop_has_it_open(project) is True
        finally:
            conn.close()
        assert desktop_has_it_open(project) is False

    def test_the_cli_refuses_to_enable_writes_while_it_is_open(
            self, project, capsys):
        from tapesift.database.connection import open_project_db
        conn = open_project_db(project)
        try:
            code = main([str(project), "--allow-writes", "--port", "8798"])
        finally:
            conn.close()
        assert code == 1
        assert "desktop" in capsys.readouterr().out.lower()


class TestPlayerNames:
    """Setting the key player and who else was involved, from a phone."""

    def test_a_name_is_trimmed_not_rejected(self):
        from tapesift.services.companion_server import clean_player
        assert clean_player("  Mark   Fletcher Jr. ") == "Mark Fletcher Jr."

    def test_an_opponent_label_survives(self):
        """Opponent names are usually unknown, so "#12 QB" is the label."""
        from tapesift.services.companion_server import clean_player
        assert clean_player("#12 QB") == "#12 QB"

    def test_a_runaway_paste_is_bounded(self):
        from tapesift.services.companion_server import (
            MAX_NAME_LEN, clean_player)
        assert len(clean_player("x" * 500)) == MAX_NAME_LEN

    def test_involved_accepts_a_string_or_a_list(self):
        from tapesift.services.companion_server import clean_involved
        assert clean_involved("Toney, Beck") == ["Toney", "Beck"]
        assert clean_involved(["Toney", "Beck"]) == ["Toney", "Beck"]

    def test_involved_dedupes_without_reordering(self):
        """The order someone typed is information; re-sorting argues back."""
        from tapesift.services.companion_server import clean_involved
        assert clean_involved("Beck, Toney, beck") == ["Beck", "Toney"]

    def test_involved_is_capped(self):
        from tapesift.services.companion_server import (
            MAX_INVOLVED, clean_involved)
        assert len(clean_involved([f"P{i}" for i in range(90)])) == MAX_INVOLVED

    def test_junk_is_empty_not_an_exception(self):
        from tapesift.services.companion_server import clean_involved
        assert clean_involved(5) == []
        assert clean_involved(None) == []

    def test_the_key_player_round_trips(self, project):
        view = ProjectView(project, writable=True)
        play = view.update_play(1, player_name="  Malachi  Toney ")
        assert play.details["player_name"] == "Malachi Toney"

    def test_clearing_a_name_removes_the_key(self, project):
        view = ProjectView(project, writable=True)
        view.update_play(1, player_name="Toney")
        play = view.update_play(1, player_name="")
        assert "player_name" not in play.details

    def test_involved_is_stored_the_way_the_desktop_stores_it(
            self, project):
        view = ProjectView(project, writable=True)
        play = view.update_play(1, other_players=["Beck", "Toney"])
        assert play.details["other_players"] == "Beck, Toney"

    def test_a_read_only_project_still_refuses(self, project):
        view = ProjectView(project, writable=False)
        with pytest.raises(PermissionError):
            view.update_play(1, player_name="Toney")
