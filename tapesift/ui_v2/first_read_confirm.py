"""The one consent screen in TapeSift.

Every other feature works on the user's own machine. First Read sends still
frames of their film to a third party, so what leaves, where it goes, and
what it costs are all stated here - before anything is sent, in plain words,
and not folded away behind a settings page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QLabel, QVBoxLayout, QWidget,
)


class FirstReadConfirm(QDialog):
    """Asks once, with the count and the cost, before a run starts."""

    def __init__(self, plan, game_name: str, provider: str = "OpenRouter",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("First Read")
        self.plan = plan

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)

        heading = QLabel("Run First Read on this game?", self)
        heading.setProperty("role", "heading")
        outer.addWidget(heading)

        already = (f", {len(plan.already_read)} already read"
                   if plan.already_read else "")
        outer.addWidget(QLabel(
            f"{game_name} - {plan.count} play"
            f"{'s' if plan.count != 1 else ''} to read{already}.", self))

        cost = QLabel(
            f"About ${plan.estimated_cost_usd:.2f}"
            f"   ({plan.count * 2} requests)", self)
        cost.setProperty("role", "figure")
        outer.addWidget(cost)

        # Named for what it does rather than what it is: "sends still frames
        # of these plays" is checkable by the reader, "uses a cloud model"
        # is not.
        consent = QLabel(
            f"This sends still frames of these plays to {provider} using "
            f"your own API key. The frames go from this machine to your "
            f"provider - they never pass through TapeSift. No other feature "
            f"contacts the network.", self)
        consent.setWordWrap(True)
        consent.setObjectName("FirstReadConsent")
        consent.setFrameShape(QFrame.Shape.StyledPanel)
        consent.setStyleSheet(
            "QLabel#FirstReadConsent{background:#151713;border:1px solid "
            "#2c2d27;border-left:2px solid #e0b341;border-radius:3px;"
            "padding:10px 12px;color:#8b968d;}")
        outer.addWidget(consent)

        buttons = QDialogButtonBox(self)
        self.cancel_button = buttons.addButton(
            "Not now", QDialogButtonBox.ButtonRole.RejectRole)
        self.run_button = buttons.addButton(
            "Run in the background", QDialogButtonBox.ButtonRole.AcceptRole)
        self.run_button.setProperty("primary", "true")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Nothing is sent by pressing Return without reading the box.
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
