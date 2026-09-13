"""Dot-matrix wordmark: glyph coverage.

A missing glyph doesn't raise - it silently renders as a blank space, so
"LIBRARY" would have come out as "L B  R  RY" with no error anywhere. These
guard the headings the app actually draws.
"""

from __future__ import annotations

import os
import string

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.ui_core.led_wordmark import _FONT, wordmark_pixmap  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_every_letter_has_a_glyph():
    assert not [c for c in string.ascii_uppercase if c not in _FONT]


@pytest.mark.parametrize("text", ["TAPESIFT", "LIBRARY"])
def test_headings_render_without_blanks(qapp, text):
    for char in text:
        assert _FONT[char] != _FONT[" "], f"{char} renders as a blank space"
    assert not wordmark_pixmap(text).isNull()


def test_glyphs_are_five_by_seven():
    for char, rows in _FONT.items():
        assert len(rows) == 7, char
        assert all(len(r) == 5 for r in rows), char


@pytest.mark.parametrize("scheme", ["green", "silver"])
def test_both_schemes_render(qapp, scheme):
    pixmap = wordmark_pixmap("LIBRARY", scheme=scheme)
    assert pixmap.width() > 0 and pixmap.height() > 0
