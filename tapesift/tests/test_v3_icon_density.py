from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication
from tapesift.ui_v3 import icons


def test_tint_preserves_full_svg_size_on_scaled_displays(monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = QPixmap(30, 30)
    source.setDevicePixelRatio(1.5)
    source.fill(QColor('white'))
    class SourceIcon:
        def pixmap(self, size):
            return source
    monkeypatch.setattr(icons, 'icon', lambda _: SourceIcon())
    icons.tinted_icon.cache_clear()
    result = icons.tinted_icon('density-test', '#dedfd7', 20).pixmap(QSize(20, 20), 1.5)
    assert result.devicePixelRatio() == 1.5
    assert result.toImage().pixelColor(27, 27).alpha() == 255
    icons.tinted_icon.cache_clear()
