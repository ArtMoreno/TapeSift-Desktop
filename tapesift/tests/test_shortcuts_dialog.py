"""Keyboard shortcut reference content and filtering."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.ui.shortcuts_dialog import (  # noqa: E402
    SHORTCUT_SECTIONS,
    ShortcutRow,
    ShortcutsDialog,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp):
    window = ShortcutsDialog()
    yield window
    window.close()


def test_all_shortcuts_are_rendered(dialog):
    expected = sum(len(items) for _, _, items in SHORTCUT_SECTIONS)
    assert len(dialog.findChildren(ShortcutRow)) == expected
    assert dialog.result_count.text() == f"{expected} SHORTCUTS"


def test_reference_keeps_every_workflow_group(dialog):
    section_names = [name for name, _, _ in SHORTCUT_SECTIONS]
    assert section_names == ["TRANSPORT", "TIMELINE", "TAG MAP", "MARK & EDIT", "REVIEW MODE", "PROJECT"]

    keys = {row.item.keys for row in dialog.findChildren(ShortcutRow)}
    assert {
        "Space", "C", "Ctrl + K", "Ctrl + E", "Ctrl + Z  /  Ctrl + Y",
    } <= keys
    ctrl_e = next(
        row.item for row in dialog.findChildren(ShortcutRow)
        if row.item.keys == "Ctrl + E")
    assert "Quick Export" in ctrl_e.action
    # Review mode lists a second binding because keyboard utilities often
    # swallow the F row before the app sees it.
    review = next(k for k in keys if k.startswith("F5"))
    assert "Ctrl + Shift + R" in review


def test_search_filters_rows_and_updates_count(dialog):
    dialog.search.setText("split")
    matching = [row for row in dialog.findChildren(ShortcutRow) if not row.isHidden()]
    assert [row.item.keys for row in matching] == ["C", "Ctrl + K"]
    assert dialog.result_count.text() == "2 SHORTCUTS"


def test_clearing_search_restores_all_rows(dialog):
    dialog.search.setText("export")
    dialog.search.clear()
    assert all(not row.isHidden() for row in dialog.findChildren(ShortcutRow))
    expected = sum(len(items) for _, _, items in SHORTCUT_SECTIONS)
    assert dialog.result_count.text() == f"{expected} SHORTCUTS"
