"""Keep PySide's layout-item ownership in sync when moving live widgets."""

from PySide6.QtWidgets import QLayout, QWidget


def detach_widget(widget: QWidget) -> None:
    previous = widget.parentWidget()
    if previous is not None:
        root = previous.layout()
        if root is not None:
            # setParent removes the native QWidgetItem behind PySide's back.
            # removeWidget also invalidates its cached Python wrapper; without
            # that, a reused address can crash SignalManager::retrieveMetaObject.
            for layout in [root, *root.findChildren(QLayout)]:
                if layout.indexOf(widget) >= 0:
                    layout.removeWidget(widget)
                    break


def reparent_widget(widget: QWidget, parent: QWidget | None) -> None:
    if widget.parentWidget() is not parent:
        detach_widget(widget)
    widget.setParent(parent)
