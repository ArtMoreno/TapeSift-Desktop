"""Menu-opened dialogs share the TapeSift V2 visual language."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.project import Project  # noqa: E402
from tapesift.ui import about_dialog  # noqa: E402
from tapesift.ui.about_dialog import AboutDialog  # noqa: E402
from tapesift.ui.dialog_components import ActionDialog, DialogHeader  # noqa: E402
from tapesift.ui.project_settings_dialog import ProjectSettingsDialog  # noqa: E402
from tapesift.ui.settings_dialog import (  # noqa: E402
    DetailFieldLayoutDialog, SettingsDialog,
)
from tapesift.ui.tag_color_manager_dialog import TagColorManagerDialog  # noqa: E402
from tapesift.ui.welcome_dialog import WelcomeDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _texts(widget):
    return [label.text() for label in widget.findChildren(QLabel)]


def test_settings_uses_shared_header_and_primary_save(qapp):
    dialog = SettingsDialog(AppSettings())
    assert dialog.property("tapesiftDialog") is True
    assert len(dialog.findChildren(DialogHeader)) == 1
    assert "Settings" in _texts(dialog)
    primary = [button for button in dialog.findChildren(QPushButton)
               if button.property("primary") == "true"]
    assert any(button.text() == "Save settings" for button in primary)


def test_settings_offers_three_play_details_density_presets(qapp):
    settings = AppSettings()
    dialog = SettingsDialog(settings)
    assert [dialog.inspector_density_combo.itemData(index)
            for index in range(dialog.inspector_density_combo.count())] == [
                "compact", "comfortable", "spacious"]
    dialog.inspector_density_combo.setCurrentIndex(2)
    dialog._save()
    assert settings.inspector_density == "spacious"


def test_play_details_layout_dialog_reorders_hides_and_relabels(qapp):
    settings = AppSettings(
        detail_field_order=["player_name", "quarter"],
        hidden_detail_fields=["ball_on"],
        detail_field_labels={"player_name": "Ball Carrier"},
    )
    dialog = DetailFieldLayoutDialog(settings)

    assert dialog.fields_list.item(0).data(
        Qt.ItemDataRole.UserRole) == "player_name"
    assert dialog.fields_list.item(0).text() == "Ball Carrier"
    dialog.label_edit.setText("Primary Player")
    dialog._move_current(1)
    dialog._save()

    assert settings.detail_field_order[:2] == ["quarter", "player_name"]
    assert settings.hidden_detail_fields == ["ball_on"]
    assert settings.detail_field_labels == {"player_name": "Primary Player"}


def test_project_settings_uses_shared_shell(qapp, tmp_path):
    project = Project(
        name="Miami vs Notre Dame", output_folder=str(tmp_path),
        source_duration_ms=3_600_000,
        quarter_markers_ms=[900_000, 1_800_000, 2_700_000])
    dialog = ProjectSettingsDialog(project)
    assert dialog.property("tapesiftDialog") is True
    assert "Project settings" in _texts(dialog)
    assert any("Example:" in text for text in _texts(dialog))
    assert dialog.q2_edit.text() == "15:00"
    assert dialog.q3_edit.text() == "30:00"
    assert dialog.q4_edit.text() == "45:00"
    assert dialog.manage_tag_styles_btn.text() == \
        "Manage timeline tag colors..."


def test_tag_color_manager_saves_primary_visibility_and_category(qapp):
    dialog = TagColorManagerDialog(
        ["Pressure", "Pass TD"],
        {"pressure": {"color": "#f59e0b"}})
    pressure = dialog._rows["pressure"]
    pressure["category"].setText("Disruption")
    pressure["primary"].setChecked(False)
    pressure["timeline"].setChecked(False)

    assert dialog.tag_styles()["pressure"] == {
        "color": "#f59e0b", "category": "Disruption",
        "primary": False, "show_on_timeline": False,
    }


def test_tag_color_manager_filters_large_tag_sets(qapp):
    dialog = TagColorManagerDialog(
        ["1", "Pass", "Pressure", "Screen Pass"],
        {"pressure": {"category": "Disruption"}})

    assert dialog.table.item(0, 0).text() == "Pressure"
    assert dialog.filter_count.text() == "4 tags"
    dialog.filter_edit.setText("disrupt")

    visible = [
        dialog.table.item(row, 0).text()
        for row in range(dialog.table.rowCount())
        if not dialog.table.isRowHidden(row)
    ]
    assert visible == ["Pressure"]
    assert dialog.filter_count.text() == "1 of 4 tags"


def test_tag_color_manager_color_chips_choose_readable_text(qapp):
    assert TagColorManagerDialog._text_color_for("#1f2937") == "#f7fbf8"
    assert TagColorManagerDialog._text_color_for("#facc15") == "#07150d"


def test_project_settings_parses_quarters_and_overtime(qapp, tmp_path):
    project = Project(
        name="Game", output_folder=str(tmp_path),
        source_duration_ms=4_000_000)
    dialog = ProjectSettingsDialog(project)
    dialog.q2_edit.setText("15:00")
    assert dialog.game_year_edit.text() == "Not set"
    dialog.game_year_edit.setValue(2025)
    dialog.q3_edit.setText("30:00")
    dialog.q4_edit.setText("45:00")
    dialog.overtime_edit.setText("55:00, 60:00")

    assert dialog._parse_quarter_markers() == [
        900_000, 1_800_000, 2_700_000, 3_300_000, 3_600_000]
    dialog.accept()
    dialog.apply_to(project)
    assert project.game_year == "2025"


def test_project_settings_rejects_out_of_order_markers(qapp, tmp_path):
    project = Project(
        name="Game", output_folder=str(tmp_path),
        source_duration_ms=4_000_000)
    dialog = ProjectSettingsDialog(project)
    dialog.q2_edit.setText("20:00")
    dialog.q3_edit.setText("10:00")
    dialog.q4_edit.setText("45:00")

    with pytest.raises(ValueError, match="chronological"):
        dialog._parse_quarter_markers()


def test_welcome_is_grouped_into_three_steps(qapp):
    dialog = WelcomeDialog()
    texts = _texts(dialog)
    assert dialog.property("tapesiftDialog") is True
    brand = dialog.findChild(QLabel, "DialogBrandLockup")
    assert brand is not None
    assert brand.property("brandAsset") == "tapesift-logo.png"
    assert brand.pixmap() and not brand.pixmap().isNull()
    assert "From full game to searchable clips." in texts
    assert {"01", "02", "03"} <= set(texts)


def test_about_shows_product_version_and_media_engine(qapp):
    dialog = AboutDialog("ffmpeg version test")
    texts = _texts(dialog)
    assert "About TapeSift" in texts
    assert "VERSION 0.7.0-alpha" in texts
    assert "Media engine" in texts
    assert any("LGPL v3 or later" in text for text in texts)
    assert any("THIRD_PARTY_NOTICES.txt" in text for text in texts)
    brand = dialog.findChild(QLabel, "DialogBrandLockup")
    assert brand is not None and brand.pixmap() and not brand.pixmap().isNull()


def test_about_dependency_links_open_official_documentation(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(about_dialog.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or True)
    dialog = AboutDialog("ffmpeg version test")
    for name in ("DependencyQtLink", "DependencyFFmpegLink"):
        button = dialog.findChild(QPushButton, name)
        assert button is not None and button.accessibleName()
        assert button.toolTip() == "Opens in the default web browser"
        button.click()
    assert opened == [about_dialog.QT_URL, about_dialog.FFMPEG_URL]


def test_action_dialog_supports_menu_confirmations(qapp):
    dialog = ActionDialog(
        title="Sort clips by start time",
        subtitle="Put clips back into film order.",
        body="Existing files are not touched.",
        confirm_text="Sort clips",
        eyebrow="EDIT  /  ORGANIZE CLIPS",
        checkbox_text="Also renumber",
        checkbox_checked=True,
    )
    assert dialog.checkbox is not None and dialog.checkbox.isChecked()
    assert any(button.text() == "Sort clips" and button.property("primary") == "true"
               for button in dialog.findChildren(QPushButton))
