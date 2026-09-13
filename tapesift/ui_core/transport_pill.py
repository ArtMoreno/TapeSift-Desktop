"""Transport pill: the transport row as a rounded, elevated panel.

The transport row used to sit in the player's layout flow as a bare strip
separated from its neighbours by hairlines. Hosting it here keeps the row
intact while MainWindowV2 mounts the complete panel in its full-width bottom
control center. The rounded, shadowed surface reads as a dedicated console
instead of naked buttons on the canvas.

It stays docked in layout flow on purpose. The controls remain visible,
never cover the film, and are independent of the player column's width.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from tapesift.ui_v2.elevation import apply_elevation


class TransportPill(QWidget):
    """Wrap the window-wide transport row in a rounded, shadowed panel."""

    resized = Signal(int)

    def __init__(self, parent: QWidget | None, row: QHBoxLayout) -> None:
        super().__init__(parent)
        self.setObjectName("TransportPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_elevation(self, "card")

        shell = QHBoxLayout(self)
        shell.setContentsMargins(11, 5, 11, 5)
        # Owns the row (and its widgets) under the pill.
        #
        # The stretch is load-bearing. Without it the row is laid out at its
        # size hint and the pill centres what is left over, so a full-width
        # deck bunches in the middle no matter how its own zones are
        # weighted - which is exactly what the old three-column row wanted
        # and the six-zone deck does not.
        shell.addLayout(row, 1)

    def minimumSizeHint(self) -> QSize:
        """Keep optional deck details from imposing a desktop-wide floor.

        The row has a compact state below 1500 px.  Returning the expanded
        row's calculated minimum here prevented Qt from ever delivering the
        narrower resize that activates that state.
        """
        hint = super().minimumSizeHint()
        return QSize(1180, hint.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit(event.size().width())

