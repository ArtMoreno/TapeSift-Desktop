"""Dock V2 - the compact lower dock, behind a reversible switch.

This is a recomposition of :class:`ControlCenterDeck`, not a second deck.
Everything that makes the current dock work - ``bind_player``, every ``set_*``
state sink, the five window signals, and all seventeen
``COMPATIBILITY_ALIASES`` - is inherited unchanged, so ``VideoPlayer`` remains
the single media authority and no playback state is duplicated here.

Only three things are overridden: how the widgets are arranged
(``_build_zones``), how they resize (``_update_responsive_state``), and the
band's height constants.

Iteration 2 scope is the shell.  Controls that the target composition has no
permanent home for are *created and kept*, parked in ``self.overflow_bay``
rather than deleted, so no object, signal, or keyboard path is lost while the
later iterations give them a flyout.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (
    QActionGroup, QColor, QFont, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tapesift.ui_core.layout_ownership import reparent_widget
from tapesift.ui_v2 import deck_icons
from tapesift.ui_v2.control_center import (
    CONTROL_CENTER_QSS,
    ControlCenterDeck,
    VoiceoverWaveform,
)
from tapesift.ui_v2.jog_window import JogWindow
from tapesift.ui_v2.tokens import COLORS, SEMANTIC, WHEEL

# The compact band keeps the console vocabulary of the current deck; only the
# proportions change.  Nothing here introduces a palette.
# Every colour below resolves from tokens.py. The band previously carried
# its own cool blue-grey palette (#171B1E, #30373C, #D8DEE4 and friends)
# against a warm chassis, which is the fault ITERATION_1_SPEC item 11
# exists to end: a hex literal here is a second palette, and a second
# palette is how "cold means where you are" stops meaning anything.
DOCK_V2_QSS = f"""
QWidget#DockV2Surface {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {WHEEL["rim_upper"]},
        stop:0.10 {WHEEL["rim_mid"]},
        stop:0.50 {COLORS["window"]},
        stop:0.88 {WHEEL["rim_mid"]},
        stop:1 {WHEEL["rim_deep"]});
    border-top: 1px solid {WHEEL["rim_hi"]};
    border-bottom: 1px solid {WHEEL["rim_deep"]};
    border-left: none;
    border-right: none;
    border-radius: 0px;
}}
QWidget#ControlCenterTopRow {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {COLORS["raised"]},
        stop:0.12 {COLORS["surface"]},
        stop:0.82 {COLORS["window"]},
        stop:1 {COLORS["canvas"]});
    border-top: 1px solid {COLORS["line_strong"]};
    border-bottom: 1px solid {COLORS["canvas"]};
}}
QWidget#DockV2Group {{
    background: transparent;
}}
QWidget#TransportPositionZone {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {WHEEL["hub_deep"]},
        stop:0.22 {COLORS["canvas"]},
        stop:1 {WHEEL["dish_deep"]});
    border: 1px solid {COLORS["line"]};
    border-top-color: {WHEEL["flute_cut"]};
    border-bottom-color: {COLORS["line_strong"]};
    border-radius: 4px;
}}
QWidget#TransportMarksGroup {{
    background: {WHEEL["dish_deep"]};
    border: 1px solid {WHEEL["rim_upper"]};
    border-top-color: {WHEEL["rim_hi"]};
    border-bottom-color: {WHEEL["rim_deep"]};
    border-radius: 5px;
}}
QFrame#TimelineViewportCluster {{
    background: {WHEEL["dish_deep"]};
    border: 1px solid {WHEEL["rim_upper"]};
    border-top-color: {WHEEL["rim_hi"]};
    border-bottom-color: {WHEEL["rim_deep"]};
    border-radius: 5px;
}}
QFrame#TimelineViewportCluster QToolButton[viewportControl="true"] {{
    min-width: 0px;
    min-height: 0px;
    max-height: 30px;
    padding: 0px 4px;
    color: {SEMANTIC["marks"]};
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {WHEEL["rim_upper"]},
        stop:0.12 {COLORS["raised"]},
        stop:0.78 {COLORS["surface"]},
        stop:1 {WHEEL["dish_deep"]});
    border: 1px solid {COLORS["line_strong"]};
    border-top-color: {WHEEL["rim_hi"]};
    border-bottom-color: {WHEEL["rim_deep"]};
    border-radius: 4px;
    font-size: 9px;
    font-weight: 600;
}}
QFrame#TimelineViewportCluster QToolButton[viewportControl="true"]:hover {{
    color: {COLORS["green"]};
    background: {COLORS["hover"]};
    border-color: {SEMANTIC["commit"]};
}}
QFrame#TimelineViewportCluster QToolButton[viewportControl="true"]:pressed {{
    background: {WHEEL["hub_deep"]};
    border-color: {COLORS["line_strong"]};
}}
QFrame#TimelineViewportCluster QToolButton[viewportControl="true"]:disabled {{
    color: {COLORS["muted"]};
    background: {COLORS["surface"]};
    border-color: {COLORS["line"]};
}}
QFrame#TimelineViewportCluster QPushButton#PredictedSnapAction,
QWidget#TransportExportZone QPushButton#PredictedSnapAction {{
    min-width: 0px;
    min-height: 0px;
    max-height: 30px;
    padding: 0px 7px;
    color: {SEMANTIC["marks"]};
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {WHEEL["rim_upper"]},
        stop:0.12 {COLORS["raised"]},
        stop:0.78 {COLORS["surface"]},
        stop:1 {WHEEL["dish_deep"]});
    border: 1px solid {COLORS["line_strong"]};
    border-top-color: {WHEEL["rim_hi"]};
    border-bottom-color: {WHEEL["rim_deep"]};
    border-radius: 4px;
    font-size: 9px;
    font-weight: 600;
}}
QFrame#TimelineViewportCluster QPushButton#PredictedSnapAction:hover,
QWidget#TransportExportZone QPushButton#PredictedSnapAction:hover {{
    background: {COLORS["hover"]};
    border-color: {SEMANTIC["commit"]};
}}
QFrame#TimelineViewportCluster QPushButton#PredictedSnapAction:pressed,
QWidget#TransportExportZone QPushButton#PredictedSnapAction:pressed {{
    background: {WHEEL["hub_deep"]};
    border-color: {COLORS["line_strong"]};
}}
/* The island: the one raised surface on the band. Direction B's whole
   thesis. Its top edge is lit so relief is carried by light, not by a
   border - B14 measures both the level step and the highlight. */
QFrame#TransportIsland {{
    background: transparent;
    border: none;
    border-radius: 0px;
}}
QLabel#DockV2Timecode {{
    color: {SEMANTIC["position"]};
    letter-spacing: 0.5px;
}}
QWidget#TransportMarksGroup QPushButton[transport="true"],
QPushButton#TransportJogToggle {{
    min-width: 0px;
    min-height: 0px;
    color: {SEMANTIC["marks"]};
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {WHEEL["rim_upper"]},
        stop:0.12 {COLORS["raised"]},
        stop:0.78 {COLORS["surface"]},
        stop:1 {WHEEL["dish_deep"]});
    border: 1px solid {COLORS["line_strong"]};
    border-top-color: {WHEEL["rim_hi"]};
    border-bottom-color: {WHEEL["rim_deep"]};
    border-radius: 4px;
    padding: 0px 8px;
    font-size: 9px;
    font-weight: 600;
}}
QWidget#TransportMarksGroup QPushButton[transport="true"]:hover,
QPushButton#TransportJogToggle:hover {{
    background: {COLORS["hover"]};
    border-color: {SEMANTIC["commit"]};
}}
QWidget#TransportMarksGroup QPushButton[transport="true"]:pressed,
QPushButton#TransportJogToggle:pressed {{
    background: {WHEEL["hub_deep"]};
    border-color: {COLORS["line_strong"]};
}}
QLabel#DockV2FrameCounter {{
    color: {COLORS["muted"]};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.5px;
}}
QLabel[markValue="true"] {{
    color: {SEMANTIC["marks"]};
    letter-spacing: 0.5px;
}}
/* Tactile keycaps: filled body, lit top edge, darker lower edge, a real
   pressed state. One radius for every control in the band. */
QToolButton[dockV2Key="true"] {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 0px;
    min-width: 0px;
    min-height: 0px;
}}
QToolButton[dockV2Key="true"]:hover {{
    background: {COLORS["raised"]};
    border-color: {COLORS["line"]};
}}
QToolButton[dockV2Key="true"]:pressed {{
    background: {COLORS["pressed"]};
    border-color: {COLORS["line_strong"]};
    padding-top: 1px;
}}
/* Play is centered and only slightly larger than its neighbors. The selected
   Option 4 row is a compact glyph strip, not a large control island. */
QToolButton#TransportPlayPause[dockV2Key="true"] {{
    background: {COLORS["raised"]};
    border: 1px solid {COLORS["line_strong"]};
    border-radius: 4px;
}}
QToolButton#TransportPlayPause[dockV2Key="true"]:hover {{
    background: {COLORS["hover"]};
    border-color: {SEMANTIC["marks"]};
    padding-top: 0px;
}}
QToolButton#TransportPlayPause[dockV2Key="true"]:pressed,
QToolButton#TransportPlayPause[dockV2Key="true"]:hover:pressed {{
    background: {COLORS["pressed"]};
    border-color: {COLORS["canvas"]};
    padding-top: 1px;
}}
QToolButton#TransportPlayPause[dockV2Key="true"][playing="true"] {{
    background: {COLORS["raised"]};
    border-color: {SEMANTIC["position"]};
}}
QToolButton[dockV2Key="true"]:disabled {{
    background: {COLORS["surface"]};
    border-color: {COLORS["surface"]};
    border-top-color: {COLORS["line"]};
    color: {COLORS["muted"]};
}}
/* One focus-ring treatment (ITERATION_2_SPEC item 4). Neutral
   line_strong, never position cyan. 1px ring 2px out: the extra
   transparent margin is reserved so Qt's focus halo paints outside
   the key without moving the key's content box. */
QToolButton[dockV2Key="true"]:focus {{
    border: 1px solid {COLORS["line_strong"]};
    border-top-color: {COLORS["line_strong"]};
}}
QToolButton#TransportPlayPause[dockV2Key="true"]:focus {{
    background: {COLORS["raised"]};
    border: 2px solid {COLORS["line_strong"]};
    padding: 0px;
}}
QPushButton[transport="true"]:hover {{
    background: {COLORS["hover"]};
    border-color: {COLORS["line"]};
}}
QPushButton[transport="true"]:pressed {{
    background: {COLORS["pressed"]};
    border-color: {COLORS["canvas"]};
}}
QPushButton[transport="true"]:disabled {{
    background: {COLORS["surface"]};
    color: {COLORS["muted"]};
    border-color: {COLORS["line"]};
}}
QPushButton[transport="true"]:focus {{
    border: 1px solid {COLORS["line_strong"]};
}}
QLabel#DockV2RateText {{
    color: {COLORS["muted"]};
    font-size: 11px;
}}
QLabel#DockV2RateText[shuttling="true"] {{
    color: {SEMANTIC["motion"]};
}}
QLabel#DockV2VoiceTimer {{
    color: {COLORS["muted"]};
    letter-spacing: 0.5px;
}}
QLabel#DockV2VoiceTimer[recording="true"] {{
    color: {COLORS["error"]};
}}
QWidget#DockV2VoicePopover {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 8px;
}}
QPushButton[speedKey="true"] {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 3px;
    color: {COLORS["muted"]};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton[speedKey="true"]:hover {{
    background: {COLORS["hover"]};
    color: {COLORS["muted_bright"]};
}}
QPushButton[speedKey="true"]:checked {{
    background: {COLORS["green_dim"]};
    border-color: {COLORS["green"]};
    color: {COLORS["green"]};
}}
/* The two commit actions on the band: + CLIP and EXPORT. Both use neon green with dark text - the band's only saturated
   fills besides Play's bone, so the eye finds "make a clip" and "take it
   out" without reading. The deck's own inline sheet wins over the app
   theme's accent rule, so the green lives here. */
QPushButton[commit="true"] {{
    background: {COLORS["green"]};
    border: 1px solid {COLORS["green"]};
    border-radius: 3px;
    color: {COLORS["green_dark"]};
    font-family: "Consolas", "Cascadia Mono";
    font-size: 7px;
    font-weight: 700;
}}
QPushButton[commit="true"]:hover {{
    background: {COLORS["green_hover"]};
    border-color: {COLORS["green_hover"]};
}}
QPushButton[commit="true"]:pressed {{
    background: {COLORS["green"]};
    border-color: {COLORS["canvas"]};
}}
QPushButton[commit="true"]:disabled {{
    background: {COLORS["green_dim"]};
    border-color: {COLORS["green_dim"]};
    color: {COLORS["muted"]};
}}
/* The overflow control. Everything the band has no permanent slot for
   is reachable from here, so nothing has to be deleted to fit. */
QToolButton#DockV2Overflow {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {WHEEL["rim_upper"]},
        stop:0.12 {COLORS["raised"]},
        stop:0.78 {COLORS["surface"]},
        stop:1 {WHEEL["dish_deep"]});
    border: 1px solid {COLORS["line_strong"]};
    border-top-color: {WHEEL["rim_hi"]};
    border-bottom-color: {WHEEL["rim_deep"]};
    border-radius: 4px;
    color: {SEMANTIC["marks"]};
    font-size: 13px;
    font-weight: 600;
    padding: 0px;
}}
QToolButton#DockV2Overflow::menu-indicator {{
    image: none;
    width: 0px;
    height: 0px;
}}
QToolButton#DockV2Overflow:hover {{
    background: {COLORS["hover"]};
    border-color: {SEMANTIC["commit"]};
    color: {COLORS["text"]};
}}
"""


class SlimShuttleLine(QWidget):
    """The thin horizontal shuttle indicator beneath the transport.

    Stands in for ``ShuttleMeter``, which is a fixed 86 px built for the old
    tall zone and cannot fit a low-profile band. Same tiny protocol -
    ``set_rate`` in, ``rate`` out - so the deck's state sink is unchanged.

    One baseline and one marker, nothing else. It is the second of exactly two
    feedback surfaces (the wheel arc is the first), and it reads from the same
    engine rate, so the two can never disagree.
    """

    SCALE_VALUES = (-8.0, -4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0, 8.0)
    HEIGHT = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShuttleMeter")
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Signed shuttle speed")
        self._rate = 0.0
        self.setAccessibleDescription(self._state_text())

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        rate = float(rate or 0.0)
        if abs(rate - self._rate) < 0.001:
            return
        self._rate = rate
        self.setAccessibleDescription(self._state_text())
        self.update()

    def _state_text(self) -> str:
        if abs(self._rate) <= 0.001:
            return "Paused"
        return f"{'Forward' if self._rate > 0 else 'Reverse'} {abs(self._rate):g}x"

    def _x_for(self, rate: float, left: float, step: float) -> float:
        """Position on the evenly-spaced ladder, interpolating between stops."""
        values = self.SCALE_VALUES
        if rate <= values[0]:
            return left
        if rate >= values[-1]:
            return left + step * (len(values) - 1)
        for index in range(len(values) - 1):
            low, high = values[index], values[index + 1]
            if low <= rate <= high:
                fraction = 0.0 if high == low else (rate - low) / (high - low)
                return left + step * (index + fraction)
        return left

    def paintEvent(self, _event) -> None:
        """Track + fill. Spec 10_SPEC_BAND_B.md: 120x6, track from
        COLORS.motion_track, fill SEMANTIC.motion. No private hexes.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["motion_track"]))
        painter.drawRoundedRect(track, 1.5, 1.5)

        rate = self._rate
        if abs(rate) <= 0.001:
            painter.end()
            return
        # Fill from centre outward; direction is which side of centre,
        # magnitude is how far. Colour only says "something is moving".
        fraction = min(1.0, abs(rate) / 8.0)
        half = track.width() / 2.0
        fill_w = max(2.0, half * fraction)
        if rate > 0:
            fill = QRectF(half, 0.0, fill_w, track.height())
        else:
            fill = QRectF(half - fill_w, 0.0, fill_w, track.height())
        painter.setBrush(QColor(SEMANTIC["motion"]))
        painter.drawRoundedRect(fill, 1.5, 1.5)
        painter.end()


class MachinedTransportSurface(QFrame):
    """The standard five-key transport artwork with live Qt hit targets.

    The selected layout is the visual source of truth.  Painting the supplied
    raster keeps its chassis, chamfers, metal relief, texture, spacing and
    green ring intact; the existing buttons remain children above it and keep
    every playback callback, tooltip and accessibility name.
    """

    PLAY_ASSET = (
        Path(__file__).resolve().parent.parent
        / "resources" / "transport" / "transport-machined-play-2x.png"
    )
    PAUSE_ASSET = (
        Path(__file__).resolve().parent.parent
        / "resources" / "transport" / "transport-machined-pause-2x.png"
    )
    ASSET_SIZE = QSize(448, 88)
    ASSET_DPR = 2.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Preserve the established lookup/QA contract while changing only
        # the surface implementation.
        self.setObjectName("TransportIsland")
        self.setFixedSize(224, 44)
        self._playing = False
        self._play_art = QPixmap(str(self.PLAY_ASSET))
        self._pause_art = QPixmap(str(self.PAUSE_ASSET))
        if self._play_art.isNull() or self._pause_art.isNull():
            missing = [
                str(path) for path, art in (
                    (self.PLAY_ASSET, self._play_art),
                    (self.PAUSE_ASSET, self._pause_art),
                ) if art.isNull()
            ]
            raise FileNotFoundError(
                "Machined transport artwork is missing: " + ", ".join(missing))
        for path, art in (
                (self.PLAY_ASSET, self._play_art),
                (self.PAUSE_ASSET, self._pause_art)):
            if art.size() != self.ASSET_SIZE:
                raise ValueError(
                    f"Machined transport artwork must be 448x88: {path}")
            # The source is already filtered for the installed 224x44 slot.
            # Mark it as a 2x asset so Qt does not shrink the 1774px concept
            # raster live and turn its metal texture into single-pixel noise.
            art.setDevicePixelRatio(self.ASSET_DPR)

    def set_playing(self, playing: bool) -> None:
        state = bool(playing)
        if state == self._playing:
            return
        self._playing = state
        self.setProperty("playing", "true" if state else "false")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        art = self._pause_art if self._playing else self._play_art
        painter.drawPixmap(0, 0, art)
        painter.end()


class DockV2Deck(ControlCenterDeck):
    """The standard compact dock: one low-profile band, wheel-centred."""

    # Settings is a window route, like export: the deck asks, the window
    # opens the one existing dialog.
    settings_requested = Signal()

    # One band instead of two rows.  MainWindowV2 derives the native bottom
    # dock height from this, so the constant is the whole contract.
    #
    # 40 is a thin strip: the wheel floats in its own panel, so the
    # band carries keys and readouts only.
    # ITERATION_1_SPEC item 1. MainWindowV2 derives the native bottom dock
    # height from this, so the constant is the whole contract: a wrong value
    # shows up as a clipped band rather than as an exception.
    DECK_HEIGHT = 46
    TOP_ROW_HEIGHT = 46
    BOTTOM_ROW_HEIGHT = 0
    RULE_HEIGHT = 28

    UTILITY_CONTENT_WIDTH = 300
    # Six keys plus the wheel: jump | frame | shuttle | WHEEL | shuttle |
    # frame | jump.
    TRANSPORT_CONTENT_WIDTH = 560
    WHEEL_BAY_WIDTH = 0  # the wheel floats; the band reserves nothing
    MARK_CONTENT_WIDTH = 300
    EXPORT_CONTENT_WIDTH = 150

    TARGET_LAYOUT_WIDTH = 1683
    COMPACT_LAYOUT_WIDTH = 520
    # Iteration 3 mounts the deck flush to the region (the telestration
    # rail no longer insets the band), so the deck's floor is the compact
    # width itself. A top-level VideoPlayer cannot show narrower than the
    # deck's minimum, so keeping this at the region's compact width stops
    # the 1180 request silently clamping to 1215 (the dead-zone this item
    # kills).
    _STRIP_INSET = 0

    # ITERATION_1_SPEC item 6. The rhythm is a scale, not a habit: every
    # control on the band is one of these heights and every gap is one of
    # these gaps. B04 and B05 fail on anything else.
    KEY_SIZE = 30
    # The locked machined artwork scales to 224x44 inside the existing 46px
    # transport band. The circular centre is therefore a true 44px control.
    PLAY_SIZE = 44
    TRANSPORT_GAP = 6
    FLANK_GAP = 10
    #: Island geometry, literal to reference/band-b.svg: the island rect is
    #: x=700, width=286, height=54. Five keys (skip, shuttle, Play, shuttle,
    #: skip) center inside it — the two frame-step keys were a post-layout
    #: addition and left with iteration B-literal. 288, not the drawing's
    #: 286: the QSS border is drawn inside the frame, so the border box is
    #: 288 and the content box is the 286 the drawing specifies.
    ISLAND_W = 224
    ISLAND_H = 44
    #: One shared content edge for every row in the region.
    CONTENT_LEFT = 10
    CONTENT_RIGHT_INSET = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        # Last rate the player reported, used only to count ladder presses.
        # Set before super().__init__ because _build_zones runs inside it.
        self._engine_rate = 0.0
        super().__init__(parent)
        # The parent pins the shared console sheet in its own __init__; append
        # the compact band's rules after it so this stays a local exception
        # rather than a change to the shared theme.
        self.setStyleSheet(CONTROL_CENTER_QSS + DOCK_V2_QSS)
        self.ensurePolished()
        self._apply_premium_geometry()
        self.setMinimumHeight(self.DECK_HEIGHT)

    def _apply_premium_geometry(self) -> None:
        """Reassert geometry after the final sheet has been polished.

        Qt recomputes min/max constraints when a stylesheet lands. These
        sizes therefore belong after the inherited and Dock V2 sheets, or the
        premium keys silently collapse back to the old 31px content hint.
        """
        controls = (
            self.skip_backward_btn, self.step_back_btn, self.rewind_btn,
            self.play_btn,
            self.fast_forward_btn, self.step_fwd_btn, self.skip_forward_btn,
        )
        for control in controls:
            control.setMinimumSize(0, 0)
            control.setMaximumSize(16777215, 16777215)
            control.setFixedSize(self.KEY_SIZE, self.KEY_SIZE)
            control.setIconSize(QSize(17, 17))
        self.play_btn.setFixedSize(self.PLAY_SIZE, self.PLAY_SIZE)
        self.play_btn.setIconSize(QSize(17, 17))
        # I23: keyboard focus must be able to land on Play so the one
        # focus-ring treatment has a real widget to paint. Space stays
        # an ApplicationShortcut; this does not steal it.
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if self.play_btn.graphicsEffect() is None:
            ring = QGraphicsDropShadowEffect(self.play_btn)
            ring.setOffset(0, 0)
            ring.setBlurRadius(8)
            ring.setColor(QColor(COLORS["line_strong"]))
            ring.setEnabled(False)
            self.play_btn.setGraphicsEffect(ring)
            self.play_btn.installEventFilter(self)
        if isinstance(self.transport_island, MachinedTransportSurface):
            self._apply_machined_hit_geometry()
        self.jog_toggle.setFixedSize(88, self.KEY_SIZE)
        self.deck_export_button.setFixedSize(104, self.KEY_SIZE)
        self.overflow_button.setFixedSize(30, self.KEY_SIZE)
        for button in getattr(self, "deck_export_style_buttons", ()):
            button.setFixedSize(60, self.KEY_SIZE)

        # Text widths belong here, not in the builders. The application
        # stylesheet lands after construction and changes the font metrics,
        # so a width measured at build time is measured against the wrong
        # font: the timecode was allocated a 41px cell and painted 97px wide,
        # straight over the frame counter. Measured after polish, it is
        # right - and fixed rather than minimum, because a minimum width
        # never reaches sizeHint and a layout allocates from sizeHint.
        self.current_label.ensurePolished()
        self.current_label.setFixedWidth(
            self.current_label.fontMetrics().horizontalAdvance("00:00:00:00")
            + 16)
        # The frame counter changes its text every frame during playback
        # ("F 1,249" -> "F 1,250"). Without a fixed width the label's hint
        # changes with it and the whole row to its right reflows - the
        # wiggle the user sees from the timecode to +Clip. Fixed to the
        # widest frame number it will ever show, same reasoning as the
        # timecode above.
        self.position_detail.ensurePolished()
        self.position_detail.setFixedWidth(
            self.position_detail.fontMetrics().horizontalAdvance("F 9,999,999")
            + 8)
        for value in (self.in_value, self.out_value):
            value.ensurePolished()
            value.setFixedWidth(
                value.fontMetrics().horizontalAdvance("OUT  00:00.000") + 16)

        # Changing a fixed width does not by itself re-run the layout that
        # already placed the widget, so the new width paints outside the old
        # cell. Invalidate the two rows whose contents just changed.
        for zone in (self.position_zone, self.marks_group):
            layout = zone.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            zone.updateGeometry()
        self._top_row.invalidate()
        self._top_row.activate()

        # Re-pin the flanks now that the text widths are final. The zone
        # widths are pinned from resizeEvent, which fires before the sheet
        # lands, so without this the position zone stays frozen at the width
        # it needed under the wrong font - measured at 107px while needing
        # 177, which is why the timecode painted over the frame counter.
        self._update_responsive_state(self.width())
        self.in_button.setFixedSize(42, self.KEY_SIZE)
        self.out_button.setFixedSize(46, self.KEY_SIZE)
        # The joined IN/OUT cassette has one exact chassis.  Leaving the
        # wrapper at its pre-polish sizeHint (66x13) clipped both live buttons
        # after the Option 1 keys grew to 30px.
        self.marks_group.setFixedSize(97, 34)
        marks_layout = self.marks_group.layout()
        if marks_layout is not None:
            marks_layout.invalidate()
            marks_layout.activate()
        self.add_clip_btn.setFixedSize(64, self.KEY_SIZE)
        for button in self.speed_buttons:
            button.setFixedSize(32, 20)
        for button in getattr(self, "deck_export_style_buttons", []):
            button.setFixedSize(60, self.KEY_SIZE)
        # Six keys at KEY_SIZE, one Play at PLAY_SIZE, six gaps between them.
        # Derived rather than written down, so item 5's resize cannot leave
        # the island off the film's centre line.
        # The island is fixed at ISLAND_W (288, literal to band-b.svg); the
        # cluster zone is the same width so the island centres on the film's
        # centre line and resizing a key can never drift it.
        self.transport_zone.setFixedWidth(self.ISLAND_W)
        # The marks group is content-sized, not pinned: compression hides
        # the IN/OUT readout at narrow widths, and a fixed or minimum
        # width here would survive that (showEvent's singleShot re-pin
        # lands after _apply_compression) and push the floor back above
        # 1180. The layout sizes it from its visible children.
        self.marks_group.setMinimumWidth(0)
        self.marks_group.setMaximumWidth(16777215)

    def minimumSizeHint(self) -> QSize:
        """The width the band needs when fully compressed.

        ITERATION_1_SPEC item 12 lowered this from the content floor to what
        the flanks need after compression; the follow-up raised it back to
        1215 because compression (3 of 5 steps) could not keep the flanks
        out of the island at 1180. ITERATION_2_SPEC item 1 completes the
        compression order (rate meter -> text, export style -> button, then
        readout/counter/JOG), so at COMPACT_LAYOUT_WIDTH each flank gets
        (1180 - 288 - 26 - 26) / 2 = 420px, and the fully-compressed flanks
        (timecode + IN/OUT/LEN/+CLIP on the left, rate text + export +
        overflow on the right) fit comfortably inside it. The floor is the
        band's own compact width, and compression guarantees the content
        fits it - a floor above that is exactly the 1180 dead zone this
        iteration exists to remove.
        """
        return QSize(
            self.COMPACT_LAYOUT_WIDTH - self._STRIP_INSET, self.DECK_HEIGHT)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The application theme completes polishing only after this deck is
        # reparented into PlayerStripSlot. Reassert once now and once after the
        # queued layout pass so global QSS cannot collapse the premium keys.
        self._apply_premium_geometry()
        QTimer.singleShot(0, self, self._apply_premium_geometry)

    def _media_button(
            self, icon_name: str, accessible_name: str,
            object_name: str) -> QToolButton:
        """A transport key wearing the band's own chrome.

        The V1 console icons carry a keycap plate and four radial tick marks
        baked into the SVG, which put a fake button inside a flat one and
        smuggled the tick vocabulary back in. Dock V2 uses glyph-only
        ``deckv2_*`` art and gets its elevation from the stylesheet instead.
        The V1 ``console_*`` files are left untouched so the default dock is
        unchanged.
        """
        button = QToolButton(self)
        button.setObjectName(object_name)
        button.setProperty("dockV2Key", "true")
        button.setIcon(deck_icons.state_icon(self._glyph(icon_name), 30))
        button.setIconSize(QSize(17, 17))
        button.setFixedSize(self.KEY_SIZE, self.KEY_SIZE)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)
        return button

    @staticmethod
    def _glyph(icon_name: str) -> str:
        """Map a V1 console icon name onto its glyph-only Dock V2 twin."""
        return icon_name.replace("console_", "deckv2_", 1)

    def _toggle_jog_window(self, shown: bool) -> None:
        """Open or close the floating wheel, leaving it where it was."""
        if shown:
            self.jog_window.show_beside(self.jog_toggle)
        else:
            self.jog_window.hide()

    def _request_speed(self, target: float) -> None:
        """Drive forward playback to `target` through the existing controller.

        `VideoPlayer` exposes no "set rate to N" entry point - only
        `shuttle_forward`, which steps the ladder one notch per press exactly
        as L does. So this presses it, rather than reaching past the engine to
        assign `_shuttle_idx` or calling `setPlaybackRate` behind its back.
        Either shortcut would fork the rate state the whole dock reads from.

        `_engine_rate` is not a second source of truth: it is the last value
        the player pushed into `set_shuttle_rate`, used only to work out how
        many presses are still needed.
        """
        player = self.bound_player
        if player is None:
            return
        from tapesift.ui_core.video_player import SHUTTLE_SPEEDS

        ladder = sorted(float(speed) for speed in SHUTTLE_SPEEDS)
        target = min(max(target, ladder[0]), ladder[-1])
        rate = self._engine_rate

        # Reverse, stopped, or already past the target: drop to the bottom of
        # the ladder first, since the controller only ever steps upward.
        if rate <= 0.0 or rate > target + 0.001:
            player.shuttle_stop()
            player.shuttle_forward()
            rate = ladder[0]

        # Bounded by the ladder length; never a while-True on engine state.
        for _ in range(len(ladder)):
            if rate >= target - 0.001:
                break
            player.shuttle_forward()
            index = min(range(len(ladder)),
                        key=lambda i: abs(ladder[i] - rate))
            rate = ladder[min(index + 1, len(ladder) - 1)]

        # A speed key is a command whose checked state belongs to the engine.
        # A repeated click must not visually clear an already-active rate.
        if abs(self._engine_rate - target) < 0.001:
            for button, speed in zip(
                    self.speed_buttons, (1.0, 2.0, 4.0, 8.0)):
                button.setChecked(abs(speed - target) < 0.001)

    def set_shuttle_rate(self, rate: float) -> None:
        """Mirror the engine's rate onto the speed row.

        The row reflects state, it never asserts it: forward rates light the
        matching key, and reverse or stopped lights none.
        """
        super().set_shuttle_rate(rate)
        self._engine_rate = float(rate or 0.0)
        # The wheel is in another window now; it still reads the one rate.
        self.jog_window.set_rate(self._engine_rate)
        for button, speed in zip(self.speed_buttons, (1.0, 2.0, 4.0, 8.0)):
            button.setChecked(
                self._engine_rate > 0.0
                and abs(self._engine_rate - speed) < 0.001)
        # ITERATION_2_SPEC item 2: the band's own rate text, warm when
        # shuttling, quiet when stopped. It reads the same engine value
        # the meter and the wheel arc do; it computes nothing. Guarded:
        # set_shuttle_rate can arrive before _build_export_group runs.
        rate_label = getattr(self, "rate_label", None)
        if rate_label is not None:
            if abs(self._engine_rate) <= 0.001:
                rate_label.setText("-")
                rate_label.setProperty("shuttling", "false")
            else:
                direction = "FWD" if self._engine_rate > 0 else "REV"
                rate_label.setText(
                    f"{direction} {abs(self._engine_rate):g}×")
                rate_label.setProperty("shuttling", "true")
            rate_label.style().unpolish(rate_label)
            rate_label.style().polish(rate_label)

    def set_marks(self, in_ms: int | None, out_ms: int | None) -> None:
        """Label the readout, so an unmarked band does not show bare dashes.

        The inherited writer sets "-" when a mark is unset. On the old deck
        those labels were hidden, so nobody saw it; on the band they are the
        readout, and two floating dashes with nothing naming them read as a
        control that has failed rather than one that is waiting.
        """
        super().set_marks(in_ms, out_ms)
        blank = "-"       # em dash: absent, not "minus"
        self.in_value.setText(
            f"IN   {self.in_value.text()}" if in_ms is not None
            else f"IN   {blank}")
        self.out_value.setText(
            f"OUT  {self.out_value.text()}" if out_ms is not None
            else f"OUT  {blank}")
        if in_ms is None and out_ms is None:
            tooltip = "Set IN and OUT before adding a clip"
        elif in_ms is None:
            tooltip = "Set an IN point before adding a clip"
        elif out_ms is None:
            tooltip = "Set an OUT point before adding a clip"
        else:
            tooltip = "Add a clip from the marked IN and OUT points (A)"
        self.add_clip_btn.setToolTip(tooltip)
        self.add_clip_btn.setAccessibleName(tooltip)

    def set_playing(self, playing: bool) -> None:
        """Keep the inherited state logic; swap in the glyph-only art.

        The parent decides Play vs Pause from the real player state and owns
        the accessible name, tooltip and property. Only the icon file differs,
        and it is re-set afterwards rather than reimplemented - the hub is
        46 px, and the key matches it.
        """
        super().set_playing(playing)
        self.play_btn.setIcon(deck_icons.state_icon(
            self._glyph("console_pause" if playing else "console_play"), 19))
        self.play_btn.setIconSize(QSize(17, 17))
        # PLAY_SIZE, not a literal: this line runs on every state change, so
        # a stale number here silently un-does item 5 the moment playback
        # starts. It measured 40x40 and fill=1.000 in the `marked` cell -
        # the key had stopped being a circle without anything else changing.
        self.play_btn.setFixedSize(self.PLAY_SIZE, self.PLAY_SIZE)
        self._apply_machined_hit_geometry()
        self.transport_island.set_playing(playing)
        self.jog_ring.set_playing(playing)

    # ------------------------------------------------------------------
    # composition
    # ------------------------------------------------------------------

    def _build_zones(self) -> None:
        # Dock V2 is a single band: collapse the inherited second row rather
        # than leaving an invisible 61px reservation behind the wheel.
        self._row_divider.hide()
        self._bottom_row_host.hide()
        self._bottom_row_host.setFixedHeight(0)
        self._deck_surface.setObjectName("DockV2Surface")

        row = self._top_row
        # ITERATION_1_SPEC item 3: one content edge for the whole
        # region, not one per row.
        row.setContentsMargins(
            self.CONTENT_LEFT, 0, self.CONTENT_RIGHT_INSET, 0)
        row.setSpacing(0)

        # Transport is centred by construction. Equal-stretch flanks either
        # side of it mean the wider right-hand group cannot drag the Play
        # key off the film's centre line, which is what a plain
        # stretch-transport-stretch row did (124px off at 1520 wide).
        self._build_overflow_bay()

        left_flank = QWidget(self)
        left_flank.setObjectName("DockV2Group")
        self._left_flank_row = QHBoxLayout(left_flank)
        self._left_flank_row.setContentsMargins(0, 0, 0, 0)
        self._left_flank_row.setSpacing(0)
        row.addWidget(left_flank, 1)

        self._build_transport_group()

        right_flank = QWidget(self)
        right_flank.setObjectName("DockV2Group")
        self._right_flank_row = QHBoxLayout(right_flank)
        self._right_flank_row.setContentsMargins(0, 0, 0, 0)
        self._right_flank_row.setSpacing(0)
        row.addWidget(right_flank, 1)

        self._build_utility_group()
        self._left_flank_row.addStretch(1)

        self._build_viewport_group()
        self._right_flank_row.addStretch(1)
        self._build_mark_speed_group()
        self._rate_divider = self._rule(self._right_flank_row, self.RULE_HEIGHT)
        self._build_export_group()

        self.set_export_style("clean")

    def _build_overflow_bay(self) -> None:
        """Hold every control the compact band has no permanent slot for.

        These are live widgets, not placeholders: ``bind_player`` still
        connects them and the window shortcuts still drive the same handlers.
        They are parked, not removed, until their flyouts land.
        """
        self.overflow_bay = QWidget(self)
        self.overflow_bay.setObjectName("DockV2OverflowBay")
        bay = QVBoxLayout(self.overflow_bay)
        bay.setContentsMargins(0, 0, 0, 0)
        bay.setSpacing(0)

        self.loop_btn = QToolButton(self.overflow_bay)
        self.loop_btn.setObjectName("TransportLoop")
        self.loop_btn.setIcon(self._plate_state_icon("console_loop", 26, "#55D17B"))
        self.loop_btn.setToolTip("Loop the marked range")
        self.loop_btn.setCheckable(True)
        self.loop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Voiceover take state and reset.
        self.voiceover_take_label = QLabel("TAKE 1  ·  --:--", self.overflow_bay)
        self.voiceover_take_label.setProperty("role", "subtle")
        self.voice_clear_button = QPushButton("CLEAR", self.overflow_bay)
        self.voice_clear_button.setProperty("transport", "true")
        self.voice_clear_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.voice_clear_button.setEnabled(False)
        self.voice_clear_button.clicked.connect(
            lambda _checked=False: self.voiceover_clear_requested.emit())

        # Export styles and the format readout, pending the compact menu.
        self.deck_export_style_buttons: list[QPushButton] = []
        self.deck_export_style_group = QButtonGroup(self)
        self.deck_export_style_group.setExclusive(True)
        for text, checked in (("SIGNATURE", False),
                              ("CLEAN", True),
                              ("VERTICAL", False)):
            button = QPushButton(text, self.overflow_bay)
            button.setObjectName(f"DeckExportStyle{text.title()}")
            button.setProperty("transport", "true")
            button.setCheckable(True)
            button.setChecked(checked)
            button.setAccessibleName(f"{text.title()} export style")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda _checked=False, style=text:
                self._request_export_style(style))
            self.deck_export_style_group.addButton(button)
            self.deck_export_style_buttons.append(button)
            bay.addWidget(button)

        self.deck_format_button = QPushButton("16:9  ·  1080p", self.overflow_bay)
        self.deck_format_button.setProperty("transport", "true")
        self.deck_format_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.deck_format_button.clicked.connect(
            lambda _checked=False: self.export_format_requested.emit())

        for widget in (self.loop_btn,
                       self.deck_format_button):
            reparent_widget(widget, self.overflow_bay)
            bay.addWidget(widget)

        self.overflow_bay.hide()

    def _build_utility_group(self) -> None:
        """Left: compact voiceover, then the small position readout."""
        group = QWidget(self)
        group.setObjectName("DockV2Group")
        group.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        column = QVBoxLayout(group)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(4)

        # VOICEOVER IS PARKED. It is built but never shown on the band:
        # capture works, and the take flyout crashed the app natively on
        # teardown, so the whole group is out of the way until it is rebuilt.
        # Every widget below still exists and every set_voiceover_* sink still
        # lands, so VoiceoverDeckCoordinator needs no knowledge of this.
        self.voiceover_record_button = QPushButton("●  REC", self.overflow_bay)
        self.voiceover_record_button.setObjectName("VoiceoverRecord")
        self.voiceover_record_button.setProperty("transport", "true")
        self.voiceover_record_button.setProperty("deckVoiceRecord", "true")
        self.voiceover_record_button.setProperty("recording", "false")
        self.voiceover_record_button.setFixedSize(72, 30)
        self.voiceover_record_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.voiceover_record_button.clicked.connect(
            lambda _checked=False: self.voiceover_record_requested.emit())
        self.voiceover_waveform = VoiceoverWaveform(self.overflow_bay)
        self.voiceover_timer_label = QLabel("--:--", self.overflow_bay)
        self.voiceover_timer_label.setObjectName("DockV2VoiceTimer")
        self.voiceover_timer_label.setProperty("mono", "true")
        self.voiceover_timer_label.setProperty("recording", "false")
        self.voiceover_timer_label.setFont(self._voice_mono())

        # POSITION keeps its own object name so the alias and every existing
        # lookup still resolve; only its footprint shrinks.
        self.position_zone = QWidget(group)
        self.position_zone.setObjectName("TransportPositionZone")
        self.position_zone.setFixedHeight(self.KEY_SIZE)
        position_row = QHBoxLayout(self.position_zone)
        position_row.setContentsMargins(10, 0, 10, 0)
        position_row.setSpacing(10)
        self.current_label = QLabel("00:00:00:00", self.position_zone)
        self.current_label.setObjectName("DockV2Timecode")
        self.current_label.setProperty("mono", "true")
        timecode_font = QFont(["Consolas", "Cascadia Mono", "Courier New"])
        timecode_font.setPointSizeF(10.0)
        timecode_font.setStyleHint(QFont.StyleHint.Monospace)
        self.current_label.setFont(timecode_font)
        # Slack, deliberately. Sized to its exact text this label truncated
        # to "00:00:01:1" the moment the font resolved a hair wider - which
        # is the fault C2-01 recorded, reappearing at a different DPI. Two
        # characters of headroom costs nothing and cannot silently lie.
        self.position_detail = QLabel("-", self.position_zone)
        self.position_detail.setObjectName("DockV2FrameCounter")
        self.position_detail.setProperty("role", "subtle")
        position_row.addWidget(self.current_label)
        position_row.addWidget(self.position_detail)
        position_row.addStretch(1)

        column.addStretch(1)
        column.addWidget(self.position_zone)
        column.addStretch(1)

        self.voiceover_zone = group
        self._left_flank_row.insertWidget(
            0, group, 0, Qt.AlignmentFlag.AlignVCenter)

    def _build_viewport_group(self) -> None:
        """Reserve one quiet cluster for the player's existing zoom tools."""
        self.viewport_group = QFrame(self)
        self.viewport_group.setObjectName("TimelineViewportCluster")
        self.viewport_group.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.viewport_group.setFixedHeight(34)
        self._viewport_row = QHBoxLayout(self.viewport_group)
        self._viewport_row.setContentsMargins(2, 2, 2, 2)
        self._viewport_row.setSpacing(2)
        self.viewport_group.hide()
        self._right_flank_row.addWidget(
            self.viewport_group, 0, Qt.AlignmentFlag.AlignVCenter)

    def mount_viewport_controls(
            self, zoom_out: QToolButton, zoom_in: QToolButton,
            fit_play: QToolButton,
            at_snap: QToolButton | None = None) -> None:
        """Place the authoritative timeline tools in the transport band.

        The controls stay owned by ``VideoPlayer`` as attributes and keep
        their original signals. Only their visible Qt parent changes, so zoom
        and Fit Play use the exact same timeline implementation and menu
        routes as before.
        """
        controls = tuple(control for control in (
            zoom_out, zoom_in, fit_play, at_snap) if control is not None)
        while self._viewport_row.count():
            item = self._viewport_row.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in controls:
                widget.hide()
        for control in controls:
            reparent_widget(control, self.viewport_group)
            control.setProperty("dockV2Viewport", "true")
            # The zoom buttons carried a standalone circular inline sheet.
            # Clear it here so the three controls read as one compact group
            # inside Dock V2 instead of three buttons from a second toolbar.
            control.setStyleSheet("")
            control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._viewport_row.addWidget(
                control, 0, Qt.AlignmentFlag.AlignVCenter)
            control.show()
        zoom_out.setFixedSize(28, self.KEY_SIZE)
        zoom_in.setFixedSize(28, self.KEY_SIZE)
        zoom_out.setIconSize(QSize(14, 14))
        zoom_in.setIconSize(QSize(14, 14))
        fit_play.setFixedSize(58, self.KEY_SIZE)
        if at_snap is not None:
            at_snap.ensurePolished()
            at_snap.setFixedSize(84, self.KEY_SIZE)
        self.viewport_group.setFixedWidth(212 if at_snap is not None else 124)
        self.viewport_group.show()
        self._update_responsive_state(self.width())

    @staticmethod
    def _voice_mono() -> QFont:
        font = QFont(["Consolas", "Cascadia Mono", "Courier New"])
        font.setPointSizeF(9.5)
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font

    def set_voiceover_take(
            self, *, take_number: int | None, duration_ms: int | None,
            waveform: tuple[float, ...] | None,
            clear_enabled: bool) -> None:
        """Mirror take state, and surface its duration on the visible timer."""
        super().set_voiceover_take(
            take_number=take_number, duration_ms=duration_ms,
            waveform=waveform, clear_enabled=clear_enabled)
        self.voiceover_timer_label.setText(
            self._voiceover_duration_text(duration_ms))

    def set_voiceover_recording(
            self, recording: bool, *, busy: bool = False) -> None:
        """Mark the timer live so the band reads as recording at a glance."""
        super().set_voiceover_recording(recording, busy=busy)
        label = self.voiceover_timer_label
        label.setProperty("recording", "true" if recording else "false")
        label.style().unpolish(label)
        label.style().polish(label)

    def _build_transport_group(self) -> None:
        """Centre: two keys, the wheel, two keys, and the thin shuttle line."""
        self.transport_zone = QWidget(self)
        self.transport_zone.setObjectName("TransportCluster")
        self.transport_zone.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        column = QVBoxLayout(self.transport_zone)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        # ITERATION_1_SPEC item 4. The island is the one raised surface on
        # the band and the reason the eye finds Play without reading a word.
        # Its width is the key row's width by construction - six keys, one
        # Play, six gaps - so resizing a key can never leave the island and
        # the film's centre line disagreeing.
        self.transport_island = MachinedTransportSurface(self.transport_zone)
        self.transport_island.setFixedSize(self.ISLAND_W, self.ISLAND_H)

        # The keys nearest the wheel are the shuttle, exactly as J and L
        # behave: each press steps the ladder 1x -> 2x -> 4x -> 8x in that
        # direction. This is the primary way the deck is driven, so it gets
        # the closest reach to the hand on the wheel. Jump sits outboard.
        self.skip_backward_btn = self._media_button(
            "console_jump_back", "Jump backward (Shift+Left)",
            "TransportSkipBackward")
        # Parked, not deleted: the layout island's five keys are
        # frame-step, shuttle, PLAY, shuttle, frame-step (verified glyph by
        # glyph against the raster of band-b.svg). The two jump keys are
        # the post-layout pair hidden from the dock; Shift+Left/Right
        # on the keyboard still jumps through the player's own shortcuts.
        reparent_widget(self.skip_backward_btn, self.overflow_bay)
        self.skip_backward_btn.hide()
        self.step_back_btn = self._media_button(
            "console_frame_back", "Step back one frame (Left)",
            "TransportFrameBackward")
        self.step_fwd_btn = self._media_button(
            "console_frame_fwd", "Step forward one frame (Right)",
            "TransportFrameForward")
        self.rewind_btn = self._media_button(
            "console_shuttle_back", "Shuttle backward - press again for "
            "2x, 4x, 8x (J)", "TransportRewind")
        self.fast_forward_btn = self._media_button(
            "console_shuttle_fwd", "Shuttle forward - press again for "
            "2x, 4x, 8x (L)", "TransportFastForward")
        self.skip_forward_btn = self._media_button(
            "console_jump_fwd", "Jump forward (Shift+Right)",
            "TransportSkipForward")
        reparent_widget(self.skip_forward_btn, self.overflow_bay)
        self.skip_forward_btn.hide()

        # The wheel moves to a floating panel. It is the largest object
        # in the transport and the least often used per session, so it
        # was costing ~108px of film height to sit idle. The strip keeps
        # a key that opens it; every gesture on it is unchanged.
        self.play_btn = self._media_button(
            "console_play", "Play playback (Space)",
            "TransportPlayPause")
        self.jog_window = JogWindow(self)
        self.jog_ring = self.jog_window.wheel
        self.jog_ring.playPauseRequested.connect(
            lambda: self.play_btn.click())
        self.wheel_host = QWidget(self.transport_zone)
        self.wheel_host.setObjectName("TransportWheelHost")
        self.wheel_host.setFixedSize(0, 0)
        self.wheel_host.hide()

        # Labelled, not a bare glyph: an unlabelled icon for a panel that
        # is not on screen is not findable, and the wheel is the one thing
        # people go looking for after it stops being permanently visible.
        self.jog_toggle = QPushButton("Jog Wheel", self)
        self.jog_toggle.setObjectName("TransportJogToggle")
        self.jog_toggle.setProperty("transport", "true")
        self.jog_toggle.setToolTip(
            "Show the jog wheel; toggle JOG again to hide it")
        self.jog_toggle.setAccessibleName(self.jog_toggle.toolTip())
        self.jog_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.jog_toggle.setCheckable(True)
        self.jog_toggle.toggled.connect(self._toggle_jog_window)
        self.jog_window.closed.connect(
            lambda: self.jog_toggle.setChecked(False))

        # Five keys, Play in the middle — literal to the layout island
        # (skip, shuttle, PLAY, shuttle, skip). The two frame-step keys
        # moved to the overflow bay. Sheet first, then the size: Qt
        # recomputes size constraints from the stylesheet when it polishes,
        # so a fixed size set beforehand is discarded. This bit us on the
        # tag chips too.
        self.jog_toggle.ensurePolished()
        self.jog_toggle.setFixedSize(88, self.KEY_SIZE)
        for control in (self.step_back_btn, self.rewind_btn,
                        self.play_btn,
                        self.fast_forward_btn, self.step_fwd_btn):
            # Sized to the icon plus the sheet's own padding. At 30px the
            # content box was narrower than a 24px icon and Qt clipped the
            # glyph away entirely, leaving blank plates.
            # Do not setStyleSheet here: an inline sheet on the widget
            # swallows DOCK_V2_QSS :hover/:pressed/:focus (I21 measured
            # hover and pressed 4px apart because only the app theme
            # survived). Padding lives in DOCK_V2_QSS.
            reparent_widget(control, self.transport_island)
            control.setIconSize(QSize(17, 17))
        self._apply_machined_hit_geometry()

        # Rate is shown by the wheel arc and the speed row. The thin
        # line was a third readout of the same number, so it is parked
        # rather than deleted: the sink still drives it.
        self.dial_zone = QWidget(self.overflow_bay)
        self.dial_zone.setObjectName("TransportDialZone")
        dial_row = QHBoxLayout(self.dial_zone)
        dial_row.setContentsMargins(0, 0, 0, 0)
        dial_row.setSpacing(0)
        self.shuttle_meter = SlimShuttleLine(self.dial_zone)
        dial_row.addWidget(self.shuttle_meter, 1)

        column.addStretch(1)
        column.addWidget(
            self.transport_island, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(1)

        self._top_row.addWidget(
            self.transport_zone, 0, Qt.AlignmentFlag.AlignVCenter)

    def _apply_machined_hit_geometry(self) -> None:
        """Align live controls to the five faces in the locked source art."""
        placements = (
            (self.step_back_btn, QRect(3, 5, 42, 34)),
            (self.rewind_btn, QRect(45, 5, 47, 34)),
            (self.play_btn, QRect(90, 0, 44, 44)),
            (self.fast_forward_btn, QRect(132, 5, 47, 34)),
            (self.step_fwd_btn, QRect(179, 5, 42, 34)),
        )
        for control, geometry in placements:
            control.setMinimumSize(0, 0)
            control.setMaximumSize(16777215, 16777215)
            control.setFixedSize(geometry.size())
            control.move(geometry.topLeft())
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            effect = control.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(control)
                control.setGraphicsEffect(effect)
            # The supplied raster supplies the visible chrome and glyphs.
            # Qt buttons stay fully live above it without repainting generic
            # rectangles over the standard artwork.
            effect.setOpacity(0.0)
            control.show()

    def _build_mark_speed_group(self) -> None:
        """Right of transport: IN / OUT / + Clip over the compact speed row."""
        self.marks_group = QWidget(self)
        self.marks_group.setObjectName("TransportMarksGroup")
        self.marks_group.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        column = QVBoxLayout(self.marks_group)
        column.setContentsMargins(4, 0, 4, 0)
        column.setSpacing(6)

        mark_row = QHBoxLayout()
        mark_row.setContentsMargins(0, 0, 0, 0)
        mark_row.setSpacing(1)
        self.in_button = self._button("IN", "Set the clip in point (I)")
        self.in_button.setProperty("transport", "true")
        self.in_button.setProperty("mark", "in")
        self.in_button.setFixedSize(42, self.KEY_SIZE)
        self.out_button = self._button("OUT", "Set the clip out point (O)")
        self.out_button.setProperty("transport", "true")
        self.out_button.setProperty("mark", "out")
        self.out_button.setFixedSize(46, self.KEY_SIZE)
        self.add_clip_btn = self._button(
            "+ Add Clip", "Set IN and OUT before adding a clip")
        # Commit styling, not transport: the layout paints + CLIP neon
        # green. "commit" is the property DOCK_V2_QSS keys the green off.
        self.add_clip_btn.setProperty("commit", "true")
        # Exact copy stays legible without widening the flank enough to shed
        # the accepted IN/OUT and rate readouts at the canonical viewport.
        self.add_clip_btn.setFixedSize(64, self.KEY_SIZE)
        self.add_clip_btn.setEnabled(False)
        # The New Clip workflow in the ledger is the visible creation route.
        # Keep this established button object alive and bound so existing
        # shortcuts/callers do not lose their action, but remove the inert
        # duplicate from the transport row and its layout width.
        reparent_widget(self.add_clip_btn, self.overflow_bay)
        self.add_clip_btn.hide()

        # IN/OUT keep their compatibility readouts. The length sink remains
        # alive for callers and tooltips, but is not shown in the narrow film
        # column: it cost the final jump key's hit area at production width.
        # Labelled placeholders. The app writes bare values into these, so
        # before anything is marked they rendered as two floating "-" with
        # nothing saying what they were.
        self.in_value = self._mono_value("IN   --:--.--")
        self.in_value.setObjectName("DockV2InOut")
        self.out_value = self._mono_value("OUT  --:--.--")
        self.out_value.setObjectName("DockV2InOutOut")
        self.len_value = self._mono_value("LEN -")
        # ITERATION_1_SPEC item 7. These existed and were hidden, so the
        # operator committed a clip without being able to read its bounds.
        # They are the readout now.
        for value in (self.in_value, self.out_value):
            # Wide enough for the longest value it will ever hold. At the
            # inherited minimum of 0 it elided its own timecode - a readout
            # that truncates the number it exists to report is worse than
            # no readout, which is what B11 caught in the `marked` cell.
            # The prefix is part of the string now, so the width has to
            # cover "OUT  00:00.000" plus slack - measured eliding at the
            # old width the moment a real out point was set.
            value.setProperty("markValue", "true")
            value.show()
        self.len_value.setMinimumWidth(44)
        self.len_value.setToolTip("Marked clip length")
        # PARKED (ITERATION_1_SPEC item 7). It was still in the mark row
        # rendering a bare "-" beside the IN/OUT readout's two, so the band
        # showed three dashes and no numbers before anything was marked.
        reparent_widget(self.len_value, self.overflow_bay)
        self.len_value.hide()

        # Keep the established IN/OUT value sink alive for compatibility, but
        # park the duplicate readout. The standard strip shows the two mark
        # actions only; the selected play's authoritative bounds remain in
        # Play Details and on the timeline.
        self.inout_readout = QWidget(self.overflow_bay)
        self.inout_readout.setObjectName("DockV2InOutReadout")
        readout = QVBoxLayout(self.inout_readout)
        readout.setContentsMargins(0, 0, 0, 0)
        readout.setSpacing(0)
        readout.addWidget(self.in_value)
        readout.addWidget(self.out_value)
        self.inout_readout.hide()

        mark_row.addWidget(self.in_button)
        mark_row.addWidget(self.out_button)
        mark_row.addStretch(1)

        speed_row = QHBoxLayout()
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.setSpacing(6)
        self.speed_buttons: list[QPushButton] = []
        self.speed_group = QButtonGroup(self)
        # Not exclusive: "no speed active" is a real engine state (stopped, or
        # shuttling in reverse), and an exclusive group cannot be cleared.
        self.speed_group.setExclusive(False)
        for speed in (1, 2, 4, 8):
            # PARKED, not deleted (ITERATION_1_SPEC item 9). Two controls
            # for one job, neither reporting the engine's actual rate; the
            # rate meter in the right flank reports it instead. Every
            # button below still exists and _request_speed still fires, so
            # nothing that looked these up has to know.
            button = QPushButton(f"{speed}x", self.overflow_bay)
            button.setObjectName(f"DockV2Speed{speed}x")
            button.setProperty("speedKey", "true")
            button.setCheckable(True)
            button.setFixedSize(32, 22)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(f"Play forward at {speed}x")
            button.setAccessibleName(button.toolTip())
            button.clicked.connect(
                lambda _checked=False, target=float(speed):
                self._request_speed(target))
            self.speed_group.addButton(button)
            self.speed_buttons.append(button)
            speed_row.addWidget(button)
        self.speed_row_layout = speed_row
        # None checked at construction. Showing 1x active while the player
        # sits stopped at rate 0 would be exactly the optimistic UI state the
        # master prompt forbids; iteration 4 drives these from engine rate.
        speed_row.addStretch(1)

        column.addStretch(1)
        column.addLayout(mark_row)
        column.addStretch(1)

        # ITERATION_1_SPEC item 7. Marking a play and reading the number you
        # marked it at were 1,400px apart. They are one action, so they sit
        # together - and the speed row does not come with them (item 9).
        # Before the flank's trailing stretch, not after it: appending puts
        # the marks hard against the island instead of beside the timecode,
        # which is the whole point of moving them.
        self._left_flank_row.insertWidget(
            self._left_flank_row.count() - 1, self.marks_group, 0,
            Qt.AlignmentFlag.AlignVCenter)

    def _build_export_group(self) -> None:
        """Far right: rate, export style, compact Export and overflow.

        ITERATION_2_SPEC items 2-3: the rate meter and the export style
        segmented control are restored to the band. They were parked, not
        deleted, so this re-parents the live widgets rather than rebuilding
        them - the aliases and signals stay the same objects.
        """
        # --- Rate meter group (item 2). Spec 10_SPEC_BAND_B.md: 120x6
        # track at 1030,72 with the rate text at baseline 99. Reads the
        # engine rate through the inherited sink (set_shuttle_rate ->
        # shuttle_meter.set_rate); computes nothing of its own.
        self.rate_group = QWidget(self)
        self.rate_group.setObjectName("TransportRateGroup")
        self.rate_group.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        rate_col = QVBoxLayout(self.rate_group)
        rate_col.setContentsMargins(0, 0, 0, 0)
        rate_col.setSpacing(4)
        reparent_widget(self.shuttle_meter, self.rate_group)
        self.shuttle_meter.setFixedSize(56, 4)
        rate_col.addWidget(self.shuttle_meter, 0, Qt.AlignmentFlag.AlignLeft)
        self.rate_label = QLabel("-", self.rate_group)
        self.rate_label.setObjectName("DockV2RateText")
        self.rate_label.setProperty("role", "subtle")
        self.rate_label.setFixedWidth(56)
        self.rate_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        rate_col.addWidget(self.rate_label, 0, Qt.AlignmentFlag.AlignLeft)
        reparent_widget(self.rate_group, self.overflow_bay)
        self.rate_group.hide()

        # --- Export style segmented control (item 3). The parked style
        # buttons are the same objects, re-parented onto the band; under
        # compression step 2 they collapse back to the single action.
        self.export_style_zone = QWidget(self)
        self.export_style_zone.setObjectName("TransportExportStyleZone")
        self.export_style_zone.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        style_row = QHBoxLayout(self.export_style_zone)
        style_row.setContentsMargins(0, 0, 0, 0)
        style_row.setSpacing(0)
        for button in self.deck_export_style_buttons:
            reparent_widget(button, self.export_style_zone)
            button.setFixedSize(60, self.KEY_SIZE)
            style_row.addWidget(button)
        self.export_style_zone.hide()

        self.deck_export_zone = QWidget(self)
        self.deck_export_zone.setObjectName("TransportExportZone")
        self.deck_export_zone.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row = QHBoxLayout(self.deck_export_zone)
        row.setContentsMargins(16, 0, 0, 0)
        row.setSpacing(14)

        self.deck_export_button = self._icon_action(
            "Export ⌄", "Export presets, package and settings",
            "DeckExportAction")
        # Commit styling: the layout paints EXPORT neon green with dark
        # text. Export is the other commit action on the band.
        self.deck_export_button.setProperty("transport", "false")
        self.deck_export_button.setProperty("commit", "true")
        self._build_export_menu()
        self.deck_export_button.clicked.connect(self._show_export_menu)
        reparent_widget(self.deck_export_button, self.overflow_bay)
        self.deck_export_button.hide()

        # Settings opens the existing dialog through the existing handler;
        # the deck never owns a second settings route.
        # Parked, not deleted: Settings is in the Export menu now, and
        # anything still driving this button keeps working.
        self.settings_button = self._icon_action(
            "Settings", "Open settings", "DockV2Settings")
        self.settings_button.clicked.connect(
            lambda _checked=False: self.settings_requested.emit())
        reparent_widget(self.settings_button, self.overflow_bay)

        # ITERATION_1_SPEC item 10. Everything the band has no permanent
        # slot for is reachable from here, which is what makes parking a
        # control an honest alternative to deleting it.
        self.overflow_button = QToolButton(self.deck_export_zone)
        self.overflow_button.setObjectName("DockV2Overflow")
        self.overflow_button.setText("")
        self.overflow_button.setIcon(
            deck_icons.state_icon("chevron_down", 14))
        self.overflow_button.setIconSize(QSize(14, 14))
        self.overflow_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.overflow_button.setToolTip(
            "Everything else: speed presets, loop, volume, voiceover")
        self.overflow_button.setAccessibleName(self.overflow_button.toolTip())
        self.overflow_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.overflow_button.setFixedSize(30, self.KEY_SIZE)
        self.overflow_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.overflow_button.setMenu(self._build_overflow_menu())

        reparent_widget(self.jog_toggle, self.deck_export_zone)
        self.jog_toggle.show()
        row.setContentsMargins(12, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.jog_toggle)
        row.addWidget(self.overflow_button)

        # The standard strip groups viewport tools and the jog launcher as one
        # compact cluster. Move the existing divider and live jog/overflow
        # zone beside Snap; the stretch follows them and absorbs the rest.
        self._right_flank_row.removeWidget(self._rate_divider)
        self._right_flank_row.insertWidget(
            1, self._rate_divider, 0, Qt.AlignmentFlag.AlignVCenter)
        self._right_flank_row.insertWidget(
            2, self.deck_export_zone, 0, Qt.AlignmentFlag.AlignVCenter)

    def _build_overflow_menu(self) -> QMenu:
        """A route to every parked control, by name.

        The parked widgets stay live and keep their own signals; these are
        entries that reach them, not copies of them. Deleting a control to
        make a layout fit is the one thing 00_BRIEF forbids outright.
        """
        menu = QMenu(self)
        menu.setObjectName("DockV2OverflowMenu")
        speeds = menu.addMenu("Playback speed")
        for button in self.speed_buttons:
            action = speeds.addAction(button.text())
            action.setToolTip(button.toolTip())
            action.triggered.connect(button.click)
        menu.addSeparator()
        jog_action = menu.addAction("Show jog wheel")
        jog_action.setToolTip(self.jog_toggle.toolTip())
        jog_action.triggered.connect(self.jog_toggle.click)
        for label, widget in (
                ("Loop the marked range", getattr(self, "loop_btn", None)),
                ("Record voiceover",
                 getattr(self, "voiceover_record_button", None)),
                ("Settings", getattr(self, "settings_button", None))):
            if widget is None:
                continue
            action = menu.addAction(label)
            action.triggered.connect(widget.click)
        menu.addSeparator()
        menu.addMenu(self.export_menu)
        return menu

    def _build_export_menu(self) -> None:
        """Every export choice the old deck showed, one click away.

        The style buttons and the format readout still exist and still carry
        the state; this menu is a second face on the same controls, so
        ExportPanel keeps driving set_export_style and nothing here owns a
        second copy of the selection.
        """
        self.export_menu = QMenu(self)
        self.export_menu.setObjectName("DockV2ExportMenu")
        self.export_menu.setTitle("Export / Package")
        self.export_style_actions: dict[str, object] = {}
        group = QActionGroup(self.export_menu)
        group.setExclusive(True)
        for label in ("Signature", "Clean", "Vertical"):
            action = self.export_menu.addAction(label)
            action.setCheckable(True)
            action.setActionGroup(group)
            action.triggered.connect(
                lambda _checked=False, style=label.upper():
                self._request_export_style(style))
            self.export_style_actions[label.lower()] = action
        self.export_menu.addSeparator()
        self.export_format_action = self.export_menu.addAction(
            "Source / Preset")
        self.export_format_action.triggered.connect(
            lambda _checked=False: self.export_format_requested.emit())
        self.export_menu.addSeparator()
        open_action = self.export_menu.addAction("Open Export Package")
        open_action.triggered.connect(
            lambda _checked=False: self.export_requested.emit())
        self.export_menu.addSeparator()
        # Settings lives here rather than beside Export: two buttons at the
        # far right for one rarely-opened dialog spent width the band needs,
        # and the menu is already where the deck's other routes are.
        settings_action = self.export_menu.addAction("Settings...")
        settings_action.triggered.connect(
            lambda _checked=False: self.settings_requested.emit())
        self.export_settings_action = settings_action

    def _show_export_menu(self) -> None:
        button = self.deck_export_button
        corner = button.mapToGlobal(button.rect().topLeft())
        size = self.export_menu.sizeHint()
        self.export_menu.exec(
            corner + QPoint(0, -size.height() - 6))

    def set_export_style(self, style: str) -> None:
        """Mirror ExportPanel's selection onto both faces of the control."""
        super().set_export_style(style)
        normalized = str(style).strip().lower()
        for name, action in getattr(self, "export_style_actions", {}).items():
            action.setChecked(name == normalized)
        action = getattr(self, "export_format_action", None)
        if action is not None:
            action.setText(self.deck_format_button.text())

    def _icon_action(
            self, caption: str, tooltip: str, object_name: str) -> QPushButton:
        """A compact captioned action in the console's own button language."""
        button = QPushButton(caption, self)
        button.setObjectName(object_name)
        button.setProperty("transport", "true")
        button.setFixedSize(52, 24)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        return button

    # ------------------------------------------------------------------
    # sizing
    # ------------------------------------------------------------------

    #: The documented compression order from 10_SPEC_BAND_B.md, in the
    #: order it is surrendered as width falls. Each step names what it
    #: gives up; a step is only taken when every step before it has been
    #: taken, so the band degrades predictably and the island never
    #: overlaps a flank.
    def _compression_steps(self):
        inline_viewport = getattr(self, "inline_viewport_group", None)
        viewport_widgets = (self.viewport_group,) + (
            (inline_viewport,) if inline_viewport is not None else ())
        return (
            # 1. Rate meter bar -> rate text only.
            ("rate meter", (self.shuttle_meter,),
             lambda: self.rate_label is not None),
            # 2. IN/OUT readout -> actions only (the whole readout container
            #    collapses: a bare sub-layout keeps reporting its hidden
            #    children's width, so the collapse has to be a widget).
            ("IN/OUT readout", (self.inout_readout,), None),
            # 3. Frame counter -> hidden.
            ("frame counter", (self.position_detail,), None),
            # 4. Timeline zoom stays on this row at the canonical shell width.
            # At the absolute compact floor the same commands remain in the
            # Playback menu, so this duplicate visual cluster can yield after
            # the less useful rate meter and duplicate readouts.
            ("timeline viewport", viewport_widgets, None),
            # 5. At the absolute compact floor the mark and transport
            #    commands outrank the duplicate position readout. The full
            #    timecode and frame counter remain visible at the canonical
            #    1708px shell.
            ("position readout", (self.position_zone,), None),
            # JOG IS NOT A COMPRESSION STEP. It was, and that was wrong:
            # the wheel floats in its own window and this key is the only
            # way to open it, so shedding the key strands the wheel with no
            # route back. A readout can be surrendered for space; the sole
            # entry point to a whole control surface cannot.
        )

    def _zone_content_width(self, zone: QWidget) -> int:
        """True content width of a zone, from a real layout pass.

        sizeHint() is unreliable for this: it is cached per layout and
        QLayoutItem still reports a hidden child's full hint through it
        (the hidden IN/OUT readout column kept claiming 104px, so the
        marks group over-reported 312px and the flank 492px against the
        420px available at the 1180 floor). Activating the zone's layout
        and measuring the rightmost visible descendant reflects the
        collapse - after activation the readout is gone and the marks
        group reports its true 202px.
        """
        layout = zone.layout()
        if layout is not None:
            layout.activate()
        right = 0
        for child in zone.findChildren(QWidget):
            if child.isVisible():
                origin = child.mapTo(zone, child.rect().topLeft())
                right = max(right, origin.x() + child.width())
        return right

    def _flank_content(self, flank: QWidget) -> int:
        """Width the flank needs for its visible zones, plus spacing.

        Each visible zone is measured after a real layout pass (see
        _zone_content_width); hidden zones are skipped entirely. The sum
        is the true need, independent of how the flank's own stretch
        distributes spare space - sizeHint() could not tell a hidden
        child from a visible one, which is what kept the compressed band
        at 492px against a 420px budget.
        """
        layout = flank.layout()
        if layout is None:
            return flank.width()
        spacing = layout.spacing()
        if spacing < 0:
            spacing = 0
        total = 0
        visible = 0
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is None or not widget.isVisible():
                continue
            total += self._zone_content_width(widget)
            visible += 1
        return total + spacing * max(0, visible - 1)

    def _apply_compression(self, width: int, left, right) -> None:
        """Hide controls in the documented order until the band fits.

        Measured, not predicted - but from real geometry, not sizeHint().
        QLayout caches sizeHint per layout and a hidden child still
        reports its full hint through QLayoutItem, so a sizeHint measure
        said the flank needed 492px against the 420 available and the
        band overflowed its own right edge at the 1180 floor. Activating
        the layouts and reading the geometry that results reflects hidden
        children correctly: the readout collapses and the marks group
        reports its true 202px.

        Everything hidden here stays alive and stays reachable from the
        overflow menu; this is layout, not deletion.
        """
        steps = self._compression_steps()
        for _label, widgets, _keep in steps:
            for widget in widgets:
                widget.setVisible(True)
        if getattr(self, "export_style_zone", None) is not None:
            # Option 4 keeps export/package actions in the inspector. The
            # live segmented control remains parked for state compatibility.
            self.export_style_zone.setVisible(False)
        if getattr(self, "shuttle_meter", None) is not None:
            self.shuttle_meter.setVisible(True)
        if getattr(self, "_rate_divider", None) is not None:
            self._rate_divider.setVisible(True)
        self._compressed_away: list[str] = []

        margins = self._top_row.contentsMargins()
        available = (width - self.ISLAND_W
                     - margins.left() - margins.right()) // 2

        for label, widgets, keep in (*steps, (None, (), None)):
            # Free the flanks from the previous width's minimums so the
            # activation below can collapse to the content that exists -
            # a stale 492px minimum from the last width would otherwise
            # hold the flank open and make the measurement lie.
            left.setMinimumWidth(0)
            right.setMinimumWidth(0)
            needed = max(self._flank_content(left),
                         self._flank_content(right))
            if needed <= available or label is None:
                return
            if label == "timeline viewport" and width >= 900 \
                    and self._flank_content(right) <= available:
                # The standard desktop shell keeps direct zoom visible. If a
                # flank is still too wide here, shed the duplicate position
                # readout next instead of undoing the saved display mode.
                continue
            for widget in widgets:
                if keep is not None and not keep():
                    continue
                widget.setVisible(False)
            if label == "rate meter":
                # The divider only separates the meter from the styles; once
                # the meter is shed it stands alone beside empty stretch and
                # reads as a stray mark. It goes with the meter.
                self._rate_divider.setVisible(False)
            self._compressed_away.append(label)

    def _update_responsive_state(self, width: int) -> None:
        """Keep the transport centred on the film at any width.

        The flanks are given the same minimum and the same stretch, so
        Qt hands them identical widths and the Play key lands on the
        centre line whatever the side groups happen to contain. Fixed
        per-zone widths did the opposite: the wider side won.

        No zone is pinned here before compression runs: a fixed width
        would survive the shed (the readout hides, the zone stays 312px)
        and push the floor back above 1180. _apply_compression measures
        the flanks themselves, which includes every zone.
        """
        left = self._left_flank_row.parentWidget()
        right = self._right_flank_row.parentWidget()
        if left is not None and right is not None:
            # Shed before balancing. Equal flank minimums keep the island
            # centred, but they cannot create room that is not there: when
            # the flanks want more than half the band each, Qt squeezes and
            # the controls OVERLAP - measured at 1265px with + Clip sliding
            # underneath the island's left edge. Compression is what stops
            # that, in the order 10_SPEC_BAND_B.md freezes, so what
            # disappears is a decision rather than whatever Qt clipped last.
            self._apply_compression(width, left, right)
            # The equal minimum is the *compressed* need, so both flanks
            # stay equal (island centred) without pinning the deck above
            # the width it was asked for. Measured from real geometry,
            # not sizeHint(): a stale hint from the previous width forced
            # the flank to 492px and overflowed the band's own right edge.
            need = max(self._flank_content(left),
                       self._flank_content(right))
            left.setMinimumWidth(need)
            right.setMinimumWidth(need)

        self._top_row.invalidate()
        self.transport_zone.updateGeometry()
        self.marks_group.updateGeometry()

    def eventFilter(self, watched, event):
        """Drive the Play key's focus halo without stealing Space.

        The halo is a QGraphicsDropShadowEffect that lives outside the
        46px disc so I23 can measure a ring. It is only on while the
        key has focus; Space remains an ApplicationShortcut.
        """
        if watched is getattr(self, "play_btn", None):
            effect = watched.graphicsEffect()
            if effect is not None:
                et = event.type()
                if et == QEvent.Type.FocusIn:
                    effect.setEnabled(True)
                elif et == QEvent.Type.FocusOut:
                    effect.setEnabled(False)
        return super().eventFilter(watched, event)


# ----------------------------------------------------------------------
# reversible switch
# ----------------------------------------------------------------------

DOCK_V2_ENV = "TAPESIFT_DOCK_V2"


def dock_v2_enabled() -> bool:
    """True unless this session explicitly opted back out of Dock V2.

    Dock V2 is the dock now, so it needs no switch to appear - including from
    the desktop shortcut, which runs this source tree directly.

    The old deck stays one environment variable away: set TAPESIFT_DOCK_V2=0
    to get ControlCenterDeck back for a session. That is the rollback path,
    and it costs nothing to keep while the remaining controls are wired up.
    """
    value = os.environ.get(DOCK_V2_ENV)
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def make_control_center(parent: QWidget | None = None) -> ControlCenterDeck:
    """Return the deck this session should use.

    Both decks satisfy the same ``attach_control_center`` contract, so the
    window, the player, and every existing caller are unaware of the choice.
    """
    return DockV2Deck(parent) if dock_v2_enabled() else ControlCenterDeck(parent)
