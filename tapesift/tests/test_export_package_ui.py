"""UI contracts for the one authoritative Export Package surface."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton  # noqa: E402

from tapesift.models.export_package import ExportStyle  # noqa: E402
from tapesift.models.export_settings import FAST_COPY  # noqa: E402
from tapesift.models.signature_template import (  # noqa: E402
    SignatureIdentity,
    SignatureTemplate,
)
from tapesift.ui.export_panel import (  # noqa: E402
    ExportPanel,
    ExportPreviewContext,
    SignatureTemplateChoice,
)
from tapesift.ui_v2.control_center import ControlCenterDeck  # noqa: E402
from tapesift.ui_v2 import main_window as main_window_module  # noqa: E402
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _free_qt_widgets(qapp):
    yield
    for widget in qapp.topLevelWidgets():
        if widget.parent() is None:
            widget.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _frame() -> QImage:
    image = QImage(320, 180, QImage.Format.Format_ARGB32)
    image.fill(QColor("#173920"))
    return image


def _template_choice() -> SignatureTemplateChoice:
    return SignatureTemplateChoice(
        SignatureTemplate(
            template_id="analyst-a",
            name="Analyst A",
            identity=SignatureIdentity(
                display_text="NICK MARSHALL",
                username_text="@NMarshall_FB",
                wordmark_text="TAPESIFT",
                accent_color="#39E07A",
                show_result=False,
            ),
        ),
        revision=3,
        selected=True,
    )


def test_export_package_has_exactly_three_exclusive_styles_and_no_slate(qapp):
    panel = ExportPanel()

    assert tuple(panel.style_cards) == (
        ExportStyle.CLEAN,
        ExportStyle.SIGNATURE,
        ExportStyle.VERTICAL,
    )
    assert panel.style_group.exclusive()
    assert panel.export_style is ExportStyle.CLEAN
    assert panel.style_cards[ExportStyle.CLEAN].isChecked()
    assert not panel.findChildren(QCheckBox, "ExportIncludeSlate")
    assert "SLATE" not in {
        button.text().strip().upper()
        for button in panel.findChildren(QPushButton)
    }
    assert all(
        card.accessibleName() and card.accessibleDescription()
        for card in panel.style_cards.values()
    )


def test_clean_keeps_existing_queue_signal_and_fast_copy_is_clean_only(qapp):
    panel = ExportPanel()
    fast = panel.preset_combo.findData(FAST_COPY.name)
    assert fast >= 0
    panel.preset_combo.setCurrentIndex(fast)

    requests: list[tuple[str, str, bool]] = []
    panel.export_requested.connect(
        lambda mode, preset, accurate:
        requests.append((mode, preset, accurate)))
    panel.export_btn.click()

    # Keep the existing queue contract: the Fast Copy preset itself owns the
    # stream-copy behavior; the legacy accurate flag remains unchanged.
    assert requests == [("both", FAST_COPY.name, True)]
    panel.set_export_style(ExportStyle.SIGNATURE)
    assert not panel.preset_combo.isEnabled()
    assert panel.preset_combo.currentData() != FAST_COPY.name
    assert not panel.export_btn.isEnabled()
    assert "exactly one enabled selected clip" in (
        panel.composited_unavailable_label.text())
    panel.set_export_style(ExportStyle.CLEAN)
    assert panel.preset_combo.currentData() == FAST_COPY.name
    assert panel.export_btn.isEnabled()


def test_template_selector_and_manage_entry_are_real_seams(qapp):
    panel = ExportPanel()
    choice = _template_choice()
    panel.set_signature_templates((choice,))
    requests: list[bool] = []
    panel.manage_templates_requested.connect(lambda: requests.append(True))

    panel.set_export_style(ExportStyle.SIGNATURE)
    panel.manage_templates_btn.click()

    assert panel.template_combo.currentData() == "analyst-a"
    assert panel.selected_template_choice() == choice
    assert requests == [True]


def test_preview_uses_real_compositor_for_clean_and_signature(qapp):
    panel = ExportPanel()
    panel.resize(1180, 500)
    panel.show()
    qapp.processEvents()
    context = ExportPreviewContext.from_image(
        _frame(),
        source_video_path=r"D:\\video\\game.mp4",
        clip_id="clip-1",
        ink_event_track=b"[]",
        play_call_situation="3rd & 7",
        play_call_concept="BECK FLEA FLICKER",
        play_call_result="TD",
        timeline_position=20,
        timeline_duration=100,
    )
    panel.set_preview_context(context)
    panel.set_composited_selection((
        SimpleNamespace(id="clip-1", enabled=True),))

    assert panel.preview_label.pixmap() is not None
    assert not panel.preview_label.pixmap().isNull()
    assert "Current decoded source frame" in panel.preview_status.text()

    panel.set_signature_templates((_template_choice(),))
    panel.voiceover_check.setChecked(False)
    panel.set_export_style(ExportStyle.SIGNATURE)

    preview = panel.preview_label.pixmap()
    assert preview is None or preview.isNull()
    assert "require Voiceover" in panel.preview_status.text()
    with pytest.raises(ValueError, match="require Voiceover"):
        panel.export_package_snapshot()
    assert not panel.export_btn.isEnabled()
    panel.focus_primary_control()
    qapp.processEvents()
    assert panel.template_combo.hasFocus()


def test_voiceover_preview_uses_persisted_summary_without_audio_blob(qapp):
    panel = ExportPanel()
    panel.resize(1180, 500)
    panel.set_signature_templates((_template_choice(),))
    panel.set_preview_context(ExportPreviewContext.from_image(
        _frame(),
        source_video_path=r"D:\\video\\game.mp4",
        clip_id="clip-1",
        ink_event_track=b"[]",
        voiceover_waveform=(0.0, 0.4, 1.0, 0.2),
        voiceover_take_id="take-7",
        voiceover_frame_count=1_920,
        voiceover_has_presentation_track=True,
    ))
    panel.set_composited_selection((
        SimpleNamespace(id="clip-1", enabled=True),))
    panel.set_export_style(ExportStyle.SIGNATURE)

    assert panel.voiceover_check.isEnabled()
    assert panel.voiceover_check.isChecked()
    package = panel.export_package_snapshot()
    values = {
        item.name: item.value for item in package.compositor_inputs
    }
    assert values["voiceover_audio"] == "take:take-7:audio"
    assert values["presentation_event_track"] == "take:take-7:events"
    assert values["ink_event_track"] == "take:take-7:ink"
    preview = panel.preview_label.pixmap()
    assert preview is None or preview.isNull()
    assert "Resolving Voiceover audio frame 0" in panel.preview_status.text()


def test_deck_styles_are_exclusive_and_update_the_format_readout(qapp):
    deck = ControlCenterDeck()
    styles: list[str] = []
    deck.export_style_requested.connect(styles.append)
    buttons = {button.text(): button for button in deck.deck_export_style_buttons}

    buttons["VERTICAL"].click()

    assert styles == ["VERTICAL"]
    assert buttons["VERTICAL"].isChecked()
    assert not buttons["CLEAN"].isChecked()
    assert "1080×1920" in deck.deck_format_button.text()
    deck.set_export_style("signature")
    assert buttons["SIGNATURE"].isChecked()
    assert "1920×1080" in deck.deck_format_button.text()


def test_window_handler_selects_style_then_opens_existing_export_panel(qapp):
    panel = ExportPanel()
    opened: list[bool] = []
    window = SimpleNamespace(
        export_panel=panel,
        _focus_export=lambda: opened.append(True),
    )

    MainWindowV2._deck_export_style_requested(window, "VERTICAL")

    assert panel.export_style is ExportStyle.VERTICAL
    assert opened == [True]
    assert not panel.export_btn.isEnabled()


def test_manage_entry_opens_real_manager_seam_and_refreshes(
        qapp, tmp_path, monkeypatch):
    calls: list[tuple] = []

    class _Signal:
        def connect(self, slot):
            calls.append(("connected", slot))

    class _Dialog:
        def __init__(self, app_data_path, parent):
            calls.append(("dialog", app_data_path, parent))
            self.libraryChanged = _Signal()

        def exec(self):
            calls.append(("exec",))

    refreshed: list[bool] = []
    window = SimpleNamespace(
        _refresh_export_package_context=lambda: refreshed.append(True),
    )
    monkeypatch.setattr(
        main_window_module, "SignatureTemplateManagerDialog", _Dialog)
    monkeypatch.setattr(
        main_window_module.core_paths, "app_data_dir", lambda: tmp_path)

    MainWindowV2._manage_export_templates_requested(window)

    assert calls[0] == ("dialog", tmp_path, window)
    assert calls[1][0] == "connected"
    assert calls[2] == ("exec",)
    assert refreshed == [True]


def test_window_preview_loads_voiceover_summary_without_materializing_audio(
        qapp, tmp_path, monkeypatch):
    list_calls: list[tuple[int, str]] = []

    class _TemplateStore:
        def __init__(self, _path):
            pass

        def list_records(self):
            return ()

        def close(self):
            pass

    class _VoiceoverStore:
        def __init__(self, _conn):
            pass

        def list_summaries_for_clip(self, project_id, clip_id):
            list_calls.append((project_id, clip_id))
            return (SimpleNamespace(
                id="take-summary",
                selected=True,
                frame_count=1_440,
                has_presentation_track=True,
                waveform=(0.1, 0.7, 0.3),
                label="TAKE 1",
            ),)

    clip = SimpleNamespace(
        id="clip-1",
        details={"quarter": "Q2", "down_distance": "3rd & 7"},
        duration_ms=10_000,
        start_ms=5_000,
        clip_title="Flea Flicker",
        label="Pass",
    )
    captured: list[ExportPreviewContext | None] = []
    panel = SimpleNamespace(
        set_signature_templates=lambda *_args, **_kwargs: None,
        set_composited_selection=lambda *_args, **_kwargs: None,
        set_preview_context=captured.append,
    )
    media = SimpleNamespace(position=lambda: 6_000, duration=lambda: 20_000)
    player = SimpleNamespace(
        video_widget=SimpleNamespace(current_frame_image=_frame),
        telestration_marks=lambda: [],
        player=media,
    )
    session = SimpleNamespace(
        project=SimpleNamespace(
            id=9, source_video_path=r"D:\video\game.mp4"),
        conn=object(),
        get_clip=lambda clip_id: clip if clip_id == clip.id else None,
    )
    window = SimpleNamespace(
        export_panel=panel,
        player=player,
        session=session,
        _selected_clip_id=clip.id,
        _review_export_scope_ids=(),
        _export_restore_complete=False,
        _composited_selected_clips=lambda: [clip],
        clip_list=SimpleNamespace(selected_clip_ids=lambda: [clip.id]),
    )
    monkeypatch.setattr(
        main_window_module, "SQLiteSignatureTemplateRepository",
        _TemplateStore)
    monkeypatch.setattr(
        main_window_module, "VoiceoverRepository", _VoiceoverStore)
    monkeypatch.setattr(
        main_window_module.core_paths, "app_data_dir", lambda: tmp_path)

    MainWindowV2._refresh_export_package_context(window)

    assert list_calls == [(9, "clip-1")]
    assert len(captured) == 1
    context = captured[0]
    assert context is not None
    assert context.voiceover_audio is None
    assert context.presentation_event_track is None
    assert context.voiceover_waveform == (0.1, 0.7, 0.3)
    assert context.voiceover_take_id == "take-summary"
    assert context.voiceover_frame_count == 1_440
    assert context.voiceover_has_presentation_track is True
    assert context.source_video_path == r"D:\video\game.mp4"
    assert context.clip_id == "clip-1"
