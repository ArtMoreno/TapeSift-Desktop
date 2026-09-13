"""The packaged FFmpeg notice must match the binary configuration."""

from __future__ import annotations

import pytest

from scripts.build_windows import EXCLUDES, is_lgpl_ffmpeg_build
from scripts import fetch_ffmpeg


def test_fetch_rejects_wrong_archive_before_replacing_vendor(monkeypatch):
    monkeypatch.setattr(fetch_ffmpeg, "download", lambda url: b"wrong archive")
    monkeypatch.setattr(fetch_ffmpeg, "extract", lambda data: pytest.fail("must not replace vendor"))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        fetch_ffmpeg.main()


def test_ffmpeg_license_check_accepts_version_three_lgpl_build():
    output = (
        "ffmpeg version 7.1\n"
        "configuration: --enable-version3 --enable-libopenh264 "
        "--disable-libx264\n"
    )
    assert is_lgpl_ffmpeg_build(output)


def test_ffmpeg_license_check_rejects_gpl_build():
    output = (
        "ffmpeg version 7.1\n"
        "configuration: --enable-version3 --enable-gpl --enable-libx264\n"
    )
    assert not is_lgpl_ffmpeg_build(output)


def test_ffmpeg_license_check_rejects_nonfree_build():
    output = (
        "ffmpeg version 7.1\n"
        "configuration: --enable-version3 --enable-nonfree\n"
    )
    assert not is_lgpl_ffmpeg_build(output)


def test_ffmpeg_license_check_rejects_wrong_notice_version():
    output = (
        "ffmpeg version 7.1\n"
        "configuration: --enable-libopenh264 --disable-libx264\n"
    )
    assert not is_lgpl_ffmpeg_build(output)


def test_source_revision_is_required_for_fetch(monkeypatch, tmp_path):
    class Completed:
        stdout = (
            "ffmpeg version n7.1.5-9-gb9a218bc1e-20260721 "
            "Copyright FFmpeg\n")

    vendor = tmp_path / "ffmpeg"
    vendor.mkdir()
    (vendor / "ffmpeg.exe").touch()
    monkeypatch.setattr(fetch_ffmpeg, "VENDOR", vendor)
    monkeypatch.setattr(
        fetch_ffmpeg.subprocess, "run", lambda *args, **kwargs: Completed())
    monkeypatch.setattr(fetch_ffmpeg, "download", lambda url: b"source zip")

    name = fetch_ffmpeg.bundle_corresponding_source()

    assert name == "FFmpeg-source-b9a218bc1e.zip"
    assert (vendor / name).read_bytes() == b"source zip"


def test_source_fetch_rejects_unidentifiable_build(monkeypatch, tmp_path):
    class Completed:
        stdout = "ffmpeg version unknown\n"

    vendor = tmp_path / "ffmpeg"
    vendor.mkdir()
    (vendor / "ffmpeg.exe").touch()
    monkeypatch.setattr(fetch_ffmpeg, "VENDOR", vendor)
    monkeypatch.setattr(
        fetch_ffmpeg.subprocess, "run", lambda *args, **kwargs: Completed())

    with pytest.raises(RuntimeError, match="source revision"):
        fetch_ffmpeg.bundle_corresponding_source()


def test_developer_companion_is_excluded_from_frozen_build():
    assert "tapesift.ui_v2.companion_dialog" in EXCLUDES
    assert "tapesift.services.companion_server" in EXCLUDES


def test_team_catalog_and_existing_logos_survive_frozen_build(monkeypatch, tmp_path):
    from scripts import build_windows as builder
    import os

    teams = tmp_path / "tapesift" / "resources" / "teams"
    teams.mkdir(parents=True)
    (teams / "catalog.json").write_text("[]", encoding="utf-8")
    (teams / "2390.png").write_bytes(b"test logo")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "DIST", tmp_path / "frozen")
    monkeypatch.setattr(builder, "VENDOR_FFMPEG", tmp_path / "absent-vendor")
    commands = []
    monkeypatch.setattr(builder.subprocess, "call", lambda cmd, **kwargs: commands.append(cmd) or 0)
    assert builder.build() == 0
    assert f"{teams}{os.pathsep}tapesift/resources/teams" in commands[0]
    branding = tmp_path / "tapesift" / "resources" / "branding"
    branding.mkdir()
    commands.clear()
    assert builder.build() == 0
    assert f"{branding}{os.pathsep}tapesift/resources/branding" in commands[0]

    for relative in [*builder.REQUIRED, *(choices[0] for choices in builder.REQUIRED_ANY)]:
        target = builder.DIST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    assert not builder.verify(), "A catalog without its source logos must reject the package"
    (builder.DIST / "_internal/tapesift/resources/teams/2390.png").write_bytes(b"test logo")
    assert builder.verify()
    for name in ("broadcast-slate-texture.png", "ifi-full-name.jpg", "more-menus.png"):
        asset = builder.DIST / "_internal/tapesift/resources/branding" / name
        asset.unlink()
        assert not builder.verify(), f"Missing current branding must reject the package: {name}"
        asset.touch()
