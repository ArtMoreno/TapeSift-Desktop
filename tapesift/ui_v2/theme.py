"""Website-inspired visual system for the parallel TapeSift V2 interface."""

from __future__ import annotations

from string import Template

from tapesift.ui_v2.tokens import COLORS


V2_QSS = r"""
* {
    font-family: "Segoe UI";
    outline: none;
}
QWidget {
    background-color: $window;
    color: $text;
    font-size: 13px;
}
QMainWindow, QDialog { background-color: $canvas; }
/* Workspace panels use native Qt docking, but the chrome stays as quiet and
   compact as the panel headers already used throughout TapeSift. */
QDockWidget {
    color: $muted_bright;
    background-color: $canvas;
    border: 1px solid #34352e;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}
QDockWidget::title {
    min-height: 18px;
    max-height: 18px;
    color: $green;
    background-color: $window;
    border: none;
    border-bottom: 1px solid #34352e;
    padding: 2px 42px 2px 7px;
    text-align: left;
}
QDockWidget::close-button,
QDockWidget::float-button {
    width: 15px;
    height: 15px;
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 0;
}
QDockWidget::close-button:hover,
QDockWidget::float-button:hover {
    background-color: $green_dim;
    border-color: #356044;
}
QDockWidget::close-button:pressed,
QDockWidget::float-button:pressed {
    background-color: $green_dark;
    border-color: $green;
}
QMainWindow::separator {
    width: 1px;
    height: 1px;
    background-color: $line;
}
QMainWindow::separator:hover {
    background-color: $line_strong;
}
QLabel { background: transparent; }
QLabel[role="heading"] {
    color: $text;
    font-size: 18px;
    font-weight: 700;
}
QLabel[role="display"] {
    color: $text;
    font-size: 34px;
    font-weight: 700;
}
QLabel[role="displayAccent"] {
    color: $green;
    font-size: 34px;
    font-weight: 700;
}
QLabel[role="dialogTitle"] {
    color: $text;
    font-size: 26px;
    font-weight: 700;
}
QLabel[role="sectionTitle"] {
    color: $text;
    font-size: 15px;
    font-weight: 700;
}
QLabel[role="dialogBody"] {
    color: #dce4de;
    font-size: 13px;
    line-height: 1.35;
}
QLabel[role="eyebrow"] {
    color: $green;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel[role="subtle"] { color: $muted; }
QLabel[role="legendTitle"] {
    color: #748078;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel[role="legendItem"] { color: $muted_bright; font-size: 11px; }
/* The source viewport has one compact row above the scrubber. It is visually
   quieter than transport because moving a view is not moving the media. */
#TimelineViewportControls {
    min-height: 26px;
    max-height: 26px;
    background: transparent;
}
QToolButton[viewportControl="true"] {
    min-height: 26px;
    max-height: 26px;
    padding: 0 8px;
    color: $muted_bright;
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 5px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 600;
}
QToolButton[viewportControl="true"]:hover {
    color: $green;
    border-color: #3d7650;
}
QToolButton[viewportControl="true"]:checked {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
QToolButton[viewportControl="true"][followPlayhead="true"]:checked {
    color: #8fd8ac;
    background-color: $green_dim;
    border-color: #356044;
}
QToolButton[viewportControl="true"]:disabled {
    color: #9d998b;
    background-color: $window;
    border-color: $hover;
}
QToolButton[viewportControl="true"][fitViewport="true"] {
    padding-left: 2px;
    padding-right: 2px;
    font-size: 9px;
}
QLabel[viewportValue="true"], QLabel[viewportRange="true"] {
    min-height: 22px;
    max-height: 22px;
    color: $muted_bright;
    background-color: $window;
    border: 1px solid $line;
    border-radius: 3px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
#TimelineViewportScroll {
    min-height: 8px;
    max-height: 8px;
    background: $window;
}
#TimelineViewportScroll::handle:horizontal {
    min-width: 28px;
    background: #686555;
    border-radius: 3px;
}
#TimelineViewportScroll::handle:horizontal:hover {
    background: $green;
}
#TimelineViewportScroll::handle:horizontal:disabled {
    background: $line;
}
#TimelineViewportScroll::add-page:horizontal,
#TimelineViewportScroll::sub-page:horizontal {
    background: #18201b;
}
#TimelineViewportScroll::add-line:horizontal,
#TimelineViewportScroll::sub-line:horizontal {
    width: 0;
    height: 0;
}
/* Key and snapping stay inline with transport and share its compact height. */
QToolButton[timelineKey="true"], QComboBox[timelineColor="true"] {
    min-height: 26px;
    max-height: 26px;
    padding: 0 10px;
    border-radius: 5px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
QToolButton[timelineKey="true"], QComboBox[timelineColor="true"] {
    color: $muted_bright;
    background-color: $surface;
    border: 1px solid #424239;
}
QToolButton[timelineKey="true"]:hover,
QComboBox[timelineColor="true"]:hover {
    color: $green;
    border-color: #3d7650;
}
QToolButton[timelineKey="true"]:checked {
    color: #8fd8ac;
    border-color: #356044;
    background-color: $raised;
}
/* The transport pill: the docked bottom row wrapped in a rounded, slightly
   raised panel. Its shadow comes from elevation.py, not QSS. */
#TransportPill {
    background-color: $raised;
    border: 1px solid $line_strong;
    border-radius: 12px;
}
/* Transport buttons carry short labels in a tight row, so the general button
   padding would elide them. They opt out of it rather than growing the row.
   No min-width here: a QSS min-width silently overrides each button's
   setFixedWidth, which is exactly why the wider Play/Stop/arrows kept
   collapsing back to their ~26px text width. */
QPushButton[transport="true"] {
    min-height: 22px;
    max-height: 22px;
    padding: 0 2px;
    border-radius: 3px;
    font-size: 11px;
}
QComboBox[transport="true"] {
    min-height: 22px;
    max-height: 22px;
    padding: 0 2px 0 7px;
    border-radius: 3px;
    font-size: 11px;
}
QComboBox[transport="true"]::drop-down { width: 12px; border: none; }
QLabel[role="warning"] { color: $warning; }
QLabel[role="error"] { color: $error; }
QLabel[mono="true"] {
    color: #dce4de;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
}
QLabel[badge="true"] {
    color: $green;
    background-color: #0b1d12;
    border: 1px solid #266a40;
    border-radius: 3px;
    padding: 3px 8px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}

QPushButton, QToolButton {
    color: $text;
    background-color: $raised;
    border: 1px solid #424239;
    border-radius: 7px;
    padding: 7px 14px;
    min-height: 20px;
    font-weight: 600;
}
QPushButton:hover, QToolButton:hover {
    color: #ffffff;
    background-color: $hover;
    border-color: #686555;
}
QPushButton:pressed, QToolButton:pressed { background-color: $window; }
/* Keyboard focus was invisible: the reset sets `outline: none` everywhere.
   A green focus border makes tab navigation legible without affecting mouse
   users (buttons only show it via :focus). */
QPushButton:focus, QToolButton:focus, QComboBox:focus {
    border-color: $green;
}
/* Iteration 2: one focus-ring treatment on the band Play key.
   Neutral line_strong, 2px out. Overrides the global `outline: none`
   so a keyboard hand can find it; cold cyan stays reserved for position. */
QToolButton#TransportPlayPause:focus {
    outline: 2px solid $line_strong;
    outline-offset: 2px;
}
QPushButton:disabled, QToolButton:disabled {
    color: #647169;
    background-color: $surface;
    border-color: #34352e;
}
QPushButton[accent="true"], QToolButton[accent="true"],
QPushButton[primary="true"], QToolButton[primary="true"] {
    color: #061109;
    background-color: $green;
    border: 1px solid $green;
    font-weight: 700;
}
QPushButton[accent="true"]:hover, QToolButton[accent="true"]:hover,
QPushButton[primary="true"]:hover, QToolButton[primary="true"]:hover {
    background-color: $green_hover;
    border-color: $green_hover;
}
QPushButton[quiet="true"], QToolButton[quiet="true"] {
    background-color: transparent;
    border-color: transparent;
    color: $muted;
}
QPushButton[quiet="true"]:hover, QToolButton[quiet="true"]:hover {
    background-color: $raised;
    color: $text;
}
/* The fold handle: a full-height strip so it is still a target when the
   panel is 26px wide and there is nothing else left to click. */
QToolButton#InspectorCollapse {
    background-color: #14160f;
    border: 1px solid #26281f;
    border-radius: 3px;
    color: $muted;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 13px;
    font-weight: 700;
    min-width: 18px;
    max-width: 18px;
    min-height: 20px;
    padding: 0;
}
QToolButton#InspectorCollapse:hover {
    background-color: #1d2118;
    border-color: $green;
    color: $text;
}
/* A one-word link inheriting full button metrics cost more height than
   half the note field it governs. */
QToolButton#InspectorNotesExpand {
    padding: 0 4px;
    min-height: 0;
    max-height: 16px;
    font-size: 10px;
    letter-spacing: .5px;
}
/* The way into the Library: heavier than a header link, because searching
   every clip across every game is the thing the app is actually for. */
QPushButton[libraryEntry="true"] {
    background-color: #12201a;
    border: 1px solid #2f6b45;
    border-radius: 7px;
    padding: 6px 18px 6px 12px;
    color: #dce4de;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 17px;
    font-weight: 600;
    letter-spacing: .4px;
}
QPushButton[libraryEntry="true"]:hover {
    background-color: #17422c;
    border-color: $green;
    color: #ffffff;
}
QPushButton[workflow="true"] {
    min-width: 108px;
    padding: 9px 16px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 11px;
    letter-spacing: .6px;
}
QPushButton[workflow="true"][active="true"] {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
/* New Clip sits on the clips panel heading now, beside 11px caption text.
   At full accent size it dominated a row it is only a guest on. */
#V2ReviewClipList QToolButton[accent="true"] {
    min-height: 22px;
    max-height: 22px;
    min-width: 76px;
    padding: 0 9px;
    border-radius: 3px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .4px;
}
#V2ReviewClipList QToolButton[detectAction="true"] {
    min-height: 22px;
    max-height: 22px;
    min-width: 86px;
    padding: 0 8px;
    border-radius: 3px;
    border: 1px solid #4f5431;
    background-color: #181a13;
    color: #d9cb75;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .25px;
}
#V2ReviewClipList QToolButton[detectAction="true"]:hover {
    border-color: #d7bf45;
    background-color: #242414;
    color: #f0dd7a;
}
#V2ReviewClipList QToolButton[detectAction="true"]:pressed {
    background-color: #302d15;
}
#V2ReviewClipList QToolButton[exportAction="true"] {
    min-height: 22px;
    max-height: 22px;
    min-width: 64px;
    padding: 0 8px;
    border-radius: 3px;
    border: 1px solid #315c43;
    background-color: #111a15;
    color: #62dc91;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .25px;
}
#V2ReviewClipList QToolButton[exportAction="true"]:hover {
    border-color: $green;
    background-color: #15251b;
    color: #8af0ae;
}
#V2ReviewClipList QToolButton[exportAction="true"]:pressed {
    background-color: #1a3021;
}
#V2ReviewClipList QToolButton[exportAction="true"]:disabled {
    border-color: #29312c;
    background-color: $surface;
    color: #59635d;
}
/* Compact segmented form, for when the stages share the project row. */
QPushButton[workflowSeg="true"] {
    min-width: 0;
    min-height: 22px;
    max-height: 22px;
    padding: 0 13px;
    border-radius: 0;
    background-color: $surface;
    color: $muted;
    border: 1px solid #34352e;
}
QPushButton[workflowSeg="true"][segPos="first"] {
    border-top-left-radius: 3px;
    border-bottom-left-radius: 3px;
}
QPushButton[workflowSeg="true"][segPos="middle"] { border-left: none; }
QPushButton[workflowSeg="true"][segPos="last"] {
    border-left: none;
    border-top-right-radius: 3px;
    border-bottom-right-radius: 3px;
}
QPushButton[workflowSeg="true"]:hover { color: #dce4de; }
QPushButton[workflowSeg="true"][active="true"] {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
QPushButton[chip="true"] {
    padding: 4px 11px;
    min-height: 16px;
    border-radius: 10px;
    background-color: $raised;
    border-color: #424239;
    color: $muted_bright;
}
QPushButton[chip="true"]:checked {
    color: $green;
    background-color: #102519;
    border-color: #2a7647;
}

QWidget[hero="true"] {
    background-color: $window;
    border: 1px solid #34352e;
    border-radius: 10px;
}
QWidget[ribbon="true"] {
    background-color: $window;
    border: 1px solid #424239;
    border-radius: 10px;
}
QWidget[panel="true"] {
    /* One step above the canvas: the workspace panels read as raised
       surfaces holding the tools, with the film between them. */
    background-color: #22231e;
    border: 1px solid $line;
    border-radius: 10px;
}
QFrame[card="true"] {
    background-color: $surface;
    border: 1px solid #34352e;
    border-radius: 10px;
}
QWidget#ClipLedgerHeader, QWidget#InspectorHeader {
    /* Header band over content: the hairline gives the panel its top
       layer instead of the heading floating among the controls. */
    background: transparent;
    border-bottom: 1px solid $line;
    padding-bottom: 7px;
    margin-bottom: 4px;
}
QWidget[reviewPanel="true"] {
    background-color: $surface;
    border: 1px solid $line;
    border-radius: 10px;
}
#V2ReviewPlayerPanel {
    background-color: $window;
    border: none;
    border-radius: 0px;
}
#V2ReviewClipList,
#V2ReviewInspector {
    background-color: $surface;
    border: 1px solid $line;
    border-radius: 0px;
}
#V2ReviewClipList, #V2ReviewInspector {
    padding: 8px;
}
#V2ReviewClipList QLabel[role="heading"] {
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: .7px;
}
#ClipLedgerProgress {
    color: $muted_bright;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 11px;
}
#V2ReviewClipList QLineEdit {
    min-height: 20px;
    padding: 5px 8px;
    border-radius: 3px;
}
#V2ReviewClipList QPushButton[ledgerFilter="true"] {
    min-height: 20px;
    padding: 4px 12px;
    border-radius: 3px;
    color: $muted_bright;
    background-color: $surface;
    border-color: #424239;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 600;
}
#V2ReviewClipList QPushButton[ledgerFilter="true"]:checked {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
    font-weight: 700;
}
#V2ReviewClipList QTableWidget {
    color: #dce4de;
    background-color: $window;
    alternate-background-color: $window;
    border: none;
    border-radius: 8px;
    gridline-color: transparent;
    selection-color: $text;
    selection-background-color: #35351f;
    outline: none;
    font-family: "Segoe UI", sans-serif;
    font-size: 12px;
}
#V2ReviewClipList QTableWidget::item {
    border-bottom: 1px solid #34352e;
    padding: 3px 5px;
}
#V2ReviewClipList QTableWidget::item:selected {
    border-top: 1px solid #a79a39;
    border-bottom: 1px solid #a79a39;
}
#V2ReviewClipList QPushButton[clipNavigation="true"] {
    color: $muted_bright;
    background-color: $surface;
    border-color: #424239;
    border-radius: 3px;
    min-height: 16px;
    padding: 4px 9px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
#V2ReviewClipList QPushButton[clipNavigation="true"]:hover {
    color: $green;
    border-color: #3d7650;
}
#V2ReviewInspector[density="compact"] QLineEdit,
#V2ReviewInspector[density="compact"] QComboBox {
    min-height: 16px;
    padding: 3px 6px;
    border-radius: 3px;
}
#V2ReviewInspector[density="compact"] QPlainTextEdit {
    padding: 3px 6px;
    border-radius: 3px;
}
#V2ReviewInspector[density="compact"] QLabel[role="subtle"] {
    font-size: 11px;
}
#V2ReviewInspector #InspectorSummaryCard {
    background-color: $raised;
    border: none;
    border-radius: 8px;
}
#V2ReviewInspector QWidget#InspectorPlayRail {
    background-color: #647169;
    border: none;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="run"] {
    background-color: $green;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="pass"] {
    background-color: #4da3ff;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="rpo"] {
    background-color: #36d6c0;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="penalty"] {
    background-color: #d9b43b;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="screen"] {
    background-color: #9567d8;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="sack"] {
    background-color: #f59e0b;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="interception"] {
    background-color: #ff5d73;
}
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="touchdown"] {
    background-color: #c084fc;
}
#V2ReviewInspector QLabel#InspectorTitleSummary {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 15px;
    font-weight: 700;
}
#V2ReviewInspector QLabel#InspectorRangeSummary {
    color: $muted;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#V2ReviewInspector QPushButton#InspectorEditTitle {
    color: $muted;
    background-color: transparent;
    border: none;
    min-width: 30px;
    padding: 5px 8px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#V2ReviewInspector QPushButton#InspectorEditTitle:hover {
    color: $green;
}
#V2ReviewInspector[density="compact"] QGroupBox#ClipEditorDetails {
    margin-top: 5px;
    padding-top: 3px;
}
#V2ReviewInspector QGroupBox#ClipEditorEssentials,
#V2ReviewInspector QGroupBox#InspectorClassification,
#V2ReviewInspector QGroupBox#InspectorPlayers,
#V2ReviewInspector QGroupBox#InspectorNotes {
    background-color: $window;
    border: none;
    border-radius: 8px;
    margin-top: 7px;
    padding: 5px 4px 3px 4px;
}
#V2ReviewInspector QGroupBox#ClipEditorEssentials::title,
#V2ReviewInspector QGroupBox#InspectorClassification::title,
#V2ReviewInspector QGroupBox#InspectorPlayers::title,
#V2ReviewInspector QGroupBox#InspectorNotes::title {
    color: $muted;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    letter-spacing: .7px;
}
#V2ReviewInspector QPushButton[inspectorTag="true"] {
    min-width: 24px;
    min-height: 16px;
    padding: 3px 5px;
    color: $muted_bright;
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 3px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 8px;
    font-weight: 700;
}
#V2ReviewInspector QPushButton[inspectorTag="true"]:hover {
    color: #ffffff;
    border-color: #686555;
}
#V2ReviewInspector QPushButton[inspectorTag="true"]:checked {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
#V2ReviewInspector QToolButton#InspectorMoreDetailsToggle,
#V2ReviewInspector QToolButton#InspectorAdvancedDetailsToggle {
    color: #dce4de;
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 3px;
    padding: 7px 9px;
    text-align: left;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 700;
}
#V2ReviewInspector QToolButton#InspectorMoreDetailsToggle:hover,
#V2ReviewInspector QToolButton#InspectorAdvancedDetailsToggle:hover {
    color: $green;
    border-color: #3d7650;
}
#V2ReviewInspector QGroupBox#ClipEditorDetails[analystMore="true"] {
    background-color: transparent;
    border: none;
    margin-top: 0;
    padding-top: 0;
}
#InspectorReviewState[state="logged"] {
    color: $green;
    background-color: #102819;
    border: 1px solid #28633d;
    border-radius: 7px;
    padding: 1px 5px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#InspectorReviewState[state="unlogged"] {
    color: $muted;
    background-color: $raised;
    border: 1px solid $line_strong;
    border-radius: 7px;
    padding: 1px 5px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#InspectorSaveState[state="saved"] {
    color: $muted_bright;
    background-color: $raised;
    border: 1px solid $line_strong;
    border-radius: 7px;
    padding: 1px 5px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#InspectorSaveState[state="dirty"] {
    color: $warning;
    background-color: #241e10;
    border: 1px solid #655424;
    border-radius: 7px;
    padding: 1px 5px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#InspectorActionBar {
    background-color: $window;
    border-top: 1px solid #34352e;
}
#InspectorActionBar QPushButton#InspectorSaveNext {
    min-height: 24px;
}
#InspectorActionBar QLabel#InspectorShortcutHint {
    color: #647169;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 8px;
}
/* Selected inspector direction: responsive split scouting ledger. */
#V2ReviewInspector {
    background-color: $window;
}
#V2ReviewInspector QLabel[role="heading"] {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 19px;
    font-weight: 700;
    letter-spacing: .5px;
}
#V2ReviewInspector #InspectorSummaryCard {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #34352e;
    border-radius: 0;
}
#V2ReviewInspector QLabel#InspectorTitleSummary {
    font-size: 18px;
    letter-spacing: .2px;
}
#V2ReviewInspector QLabel#InspectorSituationSummary {
    color: $muted_bright;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}
#V2ReviewInspector QLabel#InspectorRangeSummary {
    color: #748078;
    font-size: 9px;
}
#V2ReviewInspector QWidget#InspectorLedger {
    background-color: transparent;
}
#V2ReviewInspector QWidget#InspectorLedgerLeft,
#V2ReviewInspector QWidget#InspectorLedgerRight {
    background-color: transparent;
}
#V2ReviewInspector QFrame#InspectorLedgerDivider {
    background-color: #424239;
    border: none;
}
#V2ReviewInspector QGroupBox#ClipEditorEssentials,
#V2ReviewInspector QGroupBox#InspectorClassification,
#V2ReviewInspector QGroupBox#InspectorPlayers,
#V2ReviewInspector QGroupBox#InspectorNotes {
    background-color: transparent;
    border: none;
    border-radius: 0;
    margin-top: 9px;
    padding: 4px 0 0 0;
}
#V2ReviewInspector QGroupBox#ClipEditorEssentials::title,
#V2ReviewInspector QGroupBox#InspectorClassification::title,
#V2ReviewInspector QGroupBox#InspectorPlayers::title,
#V2ReviewInspector QGroupBox#InspectorNotes::title {
    color: #6d8a78;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.4px;
    subcontrol-origin: margin;
    left: 0;
    padding: 0;
}
/* One hairline under each row instead of a filled box with a border above
   and below. The boxes made every field look like a control to operate; the
   panel reads better as a data sheet you scan and occasionally edit. */
#V2ReviewInspector QWidget[ledgerRow="true"] {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #34352e;
    min-height: 22px;
}
#V2ReviewInspector QWidget[ledgerRow="true"][ledgerStripe="true"] {
    background-color: transparent;
}
#V2ReviewInspector QWidget[ledgerRow="true"]:hover {
    background-color: $surface;
}
#V2ReviewInspector QWidget[advancedRow="true"] {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #34352e;
    min-height: 25px;
}
#V2ReviewInspector QWidget[advancedRow="true"]:hover {
    background-color: $surface;
}
#V2ReviewInspector QLabel[role="ledgerLabel"] {
    color: $muted;
    font-family: "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 600;
}
#V2ReviewInspector QLabel[role="advancedLabel"] {
    color: $muted;
    font-family: "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 600;
}
#V2ReviewInspector QComboBox[ledgerField="true"] {
    color: $text;
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 3px;
    min-height: 20px;
    padding: 2px 18px 2px 4px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 13px;
    font-weight: 700;
}
#V2ReviewInspector QComboBox[ledgerField="true"]:hover {
    background-color: $raised;
    border-color: $line_strong;
}
#V2ReviewInspector QComboBox[ledgerField="true"]:focus {
    background-color: $raised;
    border-color: $green;
}
#V2ReviewInspector QComboBox[advancedField="true"] {
    color: $text;
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 3px;
    min-height: 20px;
    padding: 2px 18px 2px 6px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 600;
}
#V2ReviewInspector QComboBox[advancedField="true"]:hover {
    border-color: #686555;
}
#V2ReviewInspector QComboBox[advancedField="true"]:focus {
    background-color: $raised;
    border-color: $green;
}
#V2ReviewInspector QWidget#InspectorClassificationChips {
    background-color: transparent;
}
#V2ReviewInspector QWidget#InspectorDetailAddRow {
    background-color: transparent;
}
/* Tag Map begins directly after the single transport band. Dock V2 carries
   the compact zoom cluster, so Review does not reserve a second toolbar. */
#TagMapHeader {
    background-color: #15191B;
    border: 1px solid #31373A;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
#TagMapHeading {
    color: $muted_bright;
    font-size: 12px;
    letter-spacing: 1px;
}
#TagMapQuickTagSlot {
    background-color: transparent;
    border: none;
}
#TagMapCollapse {
    min-height: 30px;
    color: $muted_bright;
    background-color: $surface;
    border-color: $line;
    border-radius: 6px;
}
#TagMapCollapse:focus {
    border-color: $green;
}
#V2AttributeGrid {
    border: 1px solid $line;
    border-top: none;
    border-bottom: none;
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
}
#TelestrationRail {
    background-color: $surface;
    border: 1px solid $line;
    border-radius: 8px;
}
/* One attribute, one row. Every row divides the same span, so the
   buttons line up in columns down a narrow panel. */
QWidget#V2AttributeRow {
    background-color: transparent;
}
QLabel[role="attributeRowLabel"] {
    color: #9d998b;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .5px;
}
QPushButton[attributeChoice="true"] {
    color: #eceee7;
    background-color: #1f211a;
    border: 1px solid #2f312a;
    border-radius: 4px;
    padding: 0 4px;
    min-width: 0;
    min-height: 0;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 600;
}
QPushButton[attributeChoice="true"]:hover {
    background-color: #262920;
    border-color: #4a4d43;
}
QPushButton[attributeChoice="true"]:checked {
    color: $green;
    background-color: #22301f;
    border-color: $green;
}
/* The typed slot keeps the same footprint as a preset, so the row is
   one set of equal cells rather than buttons plus an odd field. */
QLineEdit[attributeEntry="true"] {
    color: #eceee7;
    background-color: #101109;
    border: 1px solid #4a5a4e;
    border-radius: 4px;
    padding: 0 4px;
    min-height: 0;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 12px;
    font-weight: 700;
}
QLineEdit[attributeEntry="true"]:focus {
    border-color: $green;
}
QWidget#TimelineViewStrip {
    background-color: #1a1b18;
    border: 1px solid #2C3336;
    border-radius: 5px;
}
QLabel#TimelineRangeLabel {
    color: #9d998b;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .6px;
}
QFrame#ShortcutsOverlay {
    background-color: #14160f;
    border: 1px solid $green;
    border-radius: 10px;
}
QLabel#ShortcutsOverlayTitle {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 1.4px;
}
QFrame#ShortcutsOverlay QLabel[role="shortcutGroup"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1.1px;
}
/* Keycaps are tabular so a column of them lines up. */
QFrame#ShortcutsOverlay QLabel[role="shortcutKey"] {
    color: #e8efe9;
    background-color: #1d1f17;
    border: 1px solid #3e4038;
    border-radius: 4px;
    padding: 3px 9px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 12px;
    font-weight: 700;
}
QFrame#ShortcutsOverlay QLabel[role="shortcutMeaning"] {
    color: #8b968d;
    font-family: "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 600;
}
/* Add chips are an offer, not a value: outlined and quiet until hovered,
   so a row of them never competes with the details already filled in. */
#V2ReviewInspector QPushButton[detailAdd="true"] {
    color: #7f8a80;
    background-color: transparent;
    border: 1px dashed #39413a;
    border-radius: 5px;
    padding: 4px 10px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 13px;
    font-weight: 600;
    min-height: 0;
}
#V2ReviewInspector QPushButton[detailAdd="true"]:hover {
    color: #dfe8e1;
    border-color: #5c6055;
    border-style: solid;
}
#V2ReviewInspector QPushButton[detailAdd="true"]:pressed {
    color: #ffffff;
    border-color: $green;
}
#V2ReviewInspector QWidget#InspectorQuickPickers {
    background-color: transparent;
}
#V2ReviewInspector QWidget#InspectorPrimaryFields[analystRange="true"] {
    background-color: #0f1511;
    border: 1px solid #2b7a49;
    border-radius: 4px;
    padding: 5px 7px;
}
#V2ReviewInspector QWidget#InspectorPrimaryFields[analystRange="true"] QLabel {
    color: $muted_bright;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 700;
}
#V2ReviewInspector QWidget#InspectorPrimaryFields[analystRange="true"] QLineEdit {
    color: $text;
    background-color: $surface;
    border: 1px solid $line_strong;
    border-radius: 3px;
    min-height: 25px;
    padding: 3px 7px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 11px;
}
#V2ReviewInspector QLabel#InspectorRangeDuration {
    color: $muted;
    min-width: 38px;
}
#V2ReviewInspector QToolButton#InspectorMoreResults,
#V2ReviewInspector QPushButton#InspectorManageResults {
    color: $muted;
    background-color: transparent;
    border: 1px solid #424239;
    border-radius: 3px;
    min-height: 18px;
    padding: 2px 7px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 10px;
    font-weight: 700;
}
#V2ReviewInspector QToolButton#InspectorMoreResults:hover,
#V2ReviewInspector QPushButton#InspectorManageResults:hover {
    color: $green;
    border-color: #3d7650;
}
#V2ReviewInspector QWidget#InspectorNamingExportBody {
    background-color: $window;
    border: none;
}
#V2ReviewInspector QLabel[role="pickerTitle"] {
    color: #6d8a78;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.4px;
}
#V2ReviewInspector QPushButton[inspectorTag="true"] {
    min-width: 20px;
    min-height: 16px;
    padding: 3px 4px;
    border-radius: 3px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-weight: 600;
    font-size: 9px;
    letter-spacing: .2px;
}
#V2ReviewInspector QLineEdit#InspectorToGoEdit {
    color: $text;
    background-color: $surface;
    border: 1px solid $line_strong;
    border-radius: 3px;
    min-width: 42px;
    max-width: 42px;
    min-height: 18px;
    max-height: 18px;
    padding: 2px 4px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}
#V2ReviewInspector QLineEdit#InspectorToGoEdit:hover {
    border-color: #686555;
}
#V2ReviewInspector QLineEdit#InspectorToGoEdit:focus {
    background-color: $raised;
    border-color: $green;
}
#V2ReviewInspector QLabel#InspectorToGoSuffix {
    color: #748078;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 8px;
    font-weight: 600;
}
#V2ReviewInspector QGroupBox#InspectorNotes QPlainTextEdit {
    color: #dce4de;
    background-color: $window;
    border: 1px solid #424239;
    border-radius: 3px;
    padding: 6px 7px;
    font-family: "Segoe UI", sans-serif;
    font-size: 12px;
}
#V2ReviewInspector QGroupBox#InspectorNotes QPlainTextEdit:focus {
    background-color: $raised;
    border-color: $green;
}
#V2ReviewInspector QPushButton#InspectorEditTitle {
    min-width: 26px;
    min-height: 16px;
    padding: 2px 5px;
}
#InspectorActionBar {
    background-color: $window;
    border-top: 1px solid #424239;
}
#InspectorActionBar QPushButton#InspectorQuickExport {
    color: #62dc91;
    background-color: #111a15;
    border: 1px solid #315c43;
    min-width: 0;
    padding-left: 6px;
    padding-right: 6px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 700;
}
#InspectorActionBar QPushButton#InspectorQuickExport:hover {
    color: #8af0ae;
    background-color: #15251b;
    border-color: $green;
}
#InspectorActionBar QPushButton {
    border-radius: 3px;
    min-width: 0;
    min-height: 22px;
    padding: 3px 6px;
}
#InspectorActionBar QPushButton#InspectorSaveNext {
    min-width: 0;
}
#V2ReviewInspector[density="spacious"] QLineEdit,
#V2ReviewInspector[density="spacious"] QComboBox {
    min-height: 24px;
    padding: 8px 10px;
}
#V2QuickTagTray {
    background-color: $window;
    border: 1px solid #28503a;
    border-radius: 7px;
}
/* Inline in the transport row the card chrome would read as a panel
   dropped into a control strip, so it is dropped entirely. */
#V2QuickTagTray[inline="true"] {
    background: transparent;
    border: none;
    border-radius: 0px;
}
/* The overflow is a panel of the same buttons, so it keeps the card look
   the inline rail gives up. */
#V2QuickTagMorePopup {
    background-color: #12160f;
    border: 1px solid #3e4038;
    border-radius: 10px;
}
#V2QuickTagMorePopup QLabel[role="eyebrow"] {
    color: #7d857e;
    font-size: 11px;
    font-weight: 700;
    padding: 0px 0px 2px 2px;
}
#V2QuickTagMorePopup QToolButton[quickTagAdd="true"] {
    color: #8b968d;
    background: transparent;
    border: 1px dashed #4a4d42;
    border-radius: 4px;
    font-size: 17px;
    font-weight: 700;
    padding: 0px;
}
#V2QuickTagMorePopup QToolButton[quickTagAdd="true"]:hover {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
    border-style: solid;
}
#V2QuickTagMorePopup QPushButton[quickTag="true"] {
    min-height: 32px;
    padding: 7px 13px;
    border-radius: 4px;
    color: $muted_bright;
    background-color: $surface;
    border: 1px solid #34443a;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 700;
}
#V2QuickTagMorePopup QPushButton[quickTag="true"]:hover {
    color: #ffffff;
    border-color: $green;
}
#V2QuickTagMorePopup QPushButton[quickTag="true"]:checked {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
#V2QuickTagTray QLabel[role="eyebrow"] {
    color: $muted_bright;
    padding-right: 6px;
    font-size: 12px;
}
/* Compact chips. At 14px/32px the nine default tags could not fit the
   rail and were squeezed until "RPO Pass" read as "RPO Pa". They need far
   less room than they were taking. */
#V2QuickTagTray QPushButton[quickTag="true"] {
    min-height: 16px;
    padding: 1px 8px;
    border-radius: 5px;
    color: $muted_bright;
    background-color: $surface;
    border: 1px solid #34443a;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 700;
}
/* The rule between what the play was and what it produced. */
#V2QuickTagTray QFrame[quickTagDivider="true"] {
    background: #34443a;
    border: none;
}
#V2QuickTagTray QPushButton[quickTag="true"]:hover {
    color: #ffffff;
    border-color: $green;
}
#V2QuickTagTray QPushButton[quickTag="true"]:checked {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
#V2QuickTagTray QScrollArea,
#V2QuickTagTray QWidget#V2QuickTagButtonHost {
    background-color: transparent;
    border: none;
}
#V2QuickTagTray QPushButton[quickTagControl="true"],
#V2QuickTagTray QToolButton[quickTagControl="true"] {
    min-height: 32px;
    padding: 7px 13px;
    color: #9eaaa1;
    background-color: transparent;
    border: 1px solid #34443a;
    border-radius: 4px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 13px;
    font-weight: 700;
}
#V2QuickTagTray QToolButton#V2QuickTagAddButton {
    min-width: 34px;
    padding-left: 4px;
    padding-right: 4px;
    border-radius: 4px;
    color: $green;
}
#V2QuickTagTray QPushButton[quickTagControl="true"]:hover,
#V2QuickTagTray QToolButton[quickTagControl="true"]:hover {
    color: #ffffff;
    border-color: $green;
    background-color: $surface;
}
#V2QuickTagManagerDialog QLabel[role="muted"],
#V2QuickTagDefinitionDialog QLabel[role="muted"] {
    color: $muted;
}
#V2QuickTagManagerList {
    border: 1px solid #424239;
    border-radius: 5px;
    background-color: $window;
}
#V2QuickTagManagerList::item {
    min-height: 34px;
    padding: 6px 9px;
}
#V2QuickTagManagerList::item:selected {
    color: #ffffff;
    background-color: #163322;
    border-left: 2px solid $green;
}
#V2ReviewWorkbench QSplitter::handle,
#V2WorkspaceSplitter QSplitter::handle {
    background-color: $hover;
}
#V2ReviewWorkbench QSplitter::handle:hover,
#V2WorkspaceSplitter QSplitter::handle:hover {
    background-color: $line_strong;
}
QFrame[dialogSection="true"] {
    background-color: $surface;
    border: 1px solid #34352e;
    border-radius: 10px;
}
#V2NewProjectDialog {
    background-color: $surface;
    border: 1px solid $line_strong;
    border-radius: 3px;
}
#V2NewProjectDialog QWidget#NewProjectFilmColumn,
#V2NewProjectDialog QWidget#NewProjectDetailsColumn {
    background-color: transparent;
}
#V2NewProjectDialog QFrame#NewProjectHeaderRule,
#V2NewProjectDialog QFrame#NewProjectColumnDivider {
    background-color: #424239;
    border: none;
}
#V2NewProjectDialog QLabel[role="newProjectStep"],
#V2NewProjectDialog QLabel[role="newProjectFieldLabel"] {
    color: $muted_bright;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: .55px;
}
#V2NewProjectDialog QFrame[newProjectDropzone="true"] {
    background-color: $window;
    border: 1px dashed $line_strong;
    border-radius: 3px;
}
#V2NewProjectDialog QFrame[newProjectDropzone="true"][dragover="true"] {
    background-color: #102519;
    border-color: $green;
}
#V2NewProjectDialog QLabel#NewProjectFilmHeading {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 18px;
    font-weight: 700;
}
#V2NewProjectDialog QLabel#NewProjectFilmFormats {
    font-size: 12px;
}
#V2NewProjectDialog QLabel#NewProjectFilmStatus {
    min-height: 34px;
    padding: 4px 7px;
    border: 1px solid transparent;
    border-radius: 3px;
}
#V2NewProjectDialog QLabel#NewProjectFilmStatus[state="selected"] {
    color: #bff4d1;
    background-color: #102519;
    border-color: #2c6b44;
}
#V2NewProjectDialog QLabel#NewProjectFilmStatus[state="error"] {
    color: $error;
}
#V2NewProjectDialog QLineEdit {
    min-height: 25px;
    border-radius: 3px;
}
#V2NewProjectDialog QPushButton {
    min-height: 25px;
    border-radius: 3px;
}
#V2NewProjectDialog QPushButton#NewProjectFilmBrowse {
    min-width: 136px;
}
#V2NewProjectDialog QPushButton#NewProjectLocationBrowse,
#V2NewProjectDialog QPushButton#NewProjectOutputBrowse {
    min-width: 88px;
}
#V2NewProjectDialog QPushButton#NewProjectCancel,
#V2NewProjectDialog QPushButton#NewProjectCreate {
    min-width: 148px;
}
#V2NewProjectDialog QToolButton#NewProjectClose {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px;
}
#V2NewProjectDialog QToolButton#NewProjectClose:hover {
    background-color: $hover;
    border-color: $line_strong;
}
#V2NewProjectDialog QLabel#NewProjectPrivacyText {
    color: $muted_bright;
    font-size: 12px;
}
#V2NewProjectDialog QLabel#NewProjectValidation {
    color: $error;
    min-height: 18px;
}
#V2DetectPlaysDialog {
    background-color: $surface;
    border: 1px solid $line_strong;
    border-radius: 3px;
}
#V2DetectPlaysDialog QWidget,
#V2DetectPlaysDialog QStackedWidget {
    background-color: transparent;
}
#V2DetectPlaysDialog QFrame#DetectHeaderRule,
#V2DetectPlaysDialog QFrame#DetectStepRule,
#V2DetectPlaysDialog QFrame#DetectFooterRule {
    background-color: $line;
    border: none;
}
#V2DetectPlaysDialog QLabel#DetectDialogTitle {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 22px;
    font-weight: 700;
}
#V2DetectPlaysDialog QLabel#DetectStageDescription {
    color: $muted;
    font-size: 12px;
    padding-bottom: 2px;
}
#V2DetectPlaysDialog QLabel#DetectSourceName {
    font-size: 13px;
}
#V2DetectPlaysDialog QLabel#DetectBetaBadge {
    color: #e0b341;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 10px;
    font-weight: 700;
    padding-top: 4px;
}
#V2DetectPlaysDialog QToolButton#DetectDialogClose {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px;
}
#V2DetectPlaysDialog QToolButton#DetectDialogClose:hover {
    background-color: $hover;
    border-color: $line_strong;
}
#V2DetectPlaysDialog QLabel[detectStep="true"] {
    color: #77786f;
    font-size: 12px;
    min-height: 22px;
}
#V2DetectPlaysDialog QLabel[detectStep="true"][active="true"] {
    color: #52d67f;
    font-weight: 700;
}
#V2DetectPlaysDialog QFrame[detectStepDivider="true"] {
    background-color: #77786f;
    border: none;
}
#V2DetectPlaysDialog QLabel#All22DetectionNotice {
    color: #e0b341;
    background-color: $raised;
    border: 1px solid #5f542d;
    border-radius: 3px;
    padding: 10px 12px;
}
#V2DetectPlaysDialog QFrame[detectSettings="true"] {
    background-color: $window;
    border: 1px solid $line;
    border-radius: 3px;
}
#V2DetectPlaysDialog QFrame#DetectSettingsActionRule {
    background-color: $line;
    border: none;
}
#V2DetectPlaysDialog QFrame[detectSettings="true"] QDoubleSpinBox {
    min-height: 27px;
}
#V2DetectPlaysDialog QPushButton#DetectRunButton {
    color: #061109;
    background-color: $green;
    border: 1px solid $green;
    min-height: 32px;
    font-weight: 700;
}
#V2DetectPlaysDialog QPushButton#DetectRunButton:hover {
    background-color: $green_hover;
    border-color: $green_hover;
}
#V2DetectPlaysDialog QLabel#DetectRunHint {
    color: $muted;
}
#V2DetectPlaysDialog QFrame[detectAnalysisCard="true"] {
    background-color: $window;
    border: 1px solid $line;
    border-radius: 3px;
}
#V2DetectPlaysDialog QLabel#DetectAnalysisKicker {
    color: #52d67f;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.1px;
}
#V2DetectPlaysDialog QLabel#DetectAnalysisTime {
    color: #b8b4a7;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
#V2DetectPlaysDialog QWidget#DetectLiveFrameScan {
    background-color: transparent;
}
#V2DetectPlaysDialog QLabel#DetectAnalysisSource {
    color: #b8b4a7;
}
#V2DetectPlaysDialog QLabel#DetectAnalysisNote {
    color: $muted;
}
#V2DetectPlaysDialog QLabel#DetectSetupError {
    color: $error;
    min-height: 20px;
}
#V2DetectPlaysDialog QLabel#DetectAnalysisTitle,
#V2DetectPlaysDialog QLabel#DetectResultSummary {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 19px;
    font-weight: 700;
}
#V2DetectPlaysDialog QLabel#DetectAnalysisStatus,
#V2DetectPlaysDialog QLabel#DetectCoverageCaption,
#V2DetectPlaysDialog QLabel#DetectReviewRange {
    color: $muted;
}
#V2DetectPlaysDialog QLabel#DetectAnalysisStatus {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 16px;
    font-weight: 700;
}
#V2DetectPlaysDialog QProgressBar#DetectAnalysisProgress {
    min-height: 8px;
    max-height: 8px;
    border: none;
}
#V2DetectPlaysDialog QPushButton#DetectAllFilter,
#V2DetectPlaysDialog QPushButton#DetectReviewFilter {
    min-width: 88px;
    min-height: 26px;
    padding: 3px 10px;
}
#V2DetectPlaysDialog QPushButton#DetectAllFilter:checked {
    color: $green_dark;
    background-color: #52d67f;
    border-color: #52d67f;
}
#V2DetectPlaysDialog QPushButton#DetectReviewFilter:checked {
    color: $canvas;
    background-color: #e0b341;
    border-color: #e0b341;
}
#V2DetectPlaysDialog QTableWidget#DetectResultsTable,
#V2DetectPlaysDialog QTableWidget#DetectConfidentResultsTable {
    background-color: $canvas;
    alternate-background-color: $surface;
    border-color: $line;
    border-radius: 3px;
}
#V2DetectPlaysDialog QTableWidget#DetectResultsTable::item,
#V2DetectPlaysDialog QTableWidget#DetectConfidentResultsTable::item {
    padding: 5px 7px;
}
#V2DetectPlaysDialog QTableWidget#DetectResultsTable::item:selected {
    color: $text;
    background-color: #373421;
    border-left: 2px solid #e0b341;
}
#V2DetectPlaysDialog QFrame#DetectReviewDetail {
    background-color: $raised;
    border: 1px solid $line_strong;
    border-radius: 3px;
}
#V2DetectPlaysDialog QLabel[previewFrame="true"] {
    color: #77786f;
    background-color: $canvas;
    border: 1px solid $line;
    border-radius: 2px;
}
#V2DetectPlaysDialog QPushButton#DetectKeepRange,
#V2DetectPlaysDialog QPushButton#DetectDismissRange {
    min-width: 122px;
    min-height: 24px;
}
#V2DetectPlaysDialog QPushButton[selectedDecision="true"] {
    color: $canvas;
    background-color: #e0b341;
    border-color: #e0b341;
}
#V2DetectPlaysDialog QToolButton#DetectSettingsToggle {
    color: $muted_bright;
    background-color: transparent;
    border: none;
    padding: 3px 0;
}
#V2DetectPlaysDialog QToolButton#DetectSettingsToggle:hover {
    color: $text;
}
#V2DetectPlaysDialog QPushButton#DetectRunAgain {
    min-width: 98px;
}
#V2DetectPlaysDialog QPushButton#DetectBackButton,
#V2DetectPlaysDialog QPushButton#DetectCancel {
    min-width: 90px;
}
#V2DetectPlaysDialog QPushButton#DetectCreateClips {
    color: #061109;
    background-color: $green;
    border-color: $green;
    min-width: 136px;
    font-weight: 700;
}
#V2DetectPlaysDialog QPushButton#DetectCreateClips:hover {
    background-color: $green_hover;
    border-color: $green_hover;
}
#V2DetectPlaysDialog QPushButton#DetectCreateClips:disabled {
    color: #647169;
    background-color: $surface;
    border-color: #34352e;
}
QWidget[settingsPage="true"] {
    background-color: $surface;
    border: 1px solid #34352e;
    border-top: none;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}
QFrame[card="true"]:hover {
    background-color: $raised;
    border-color: #686555;
}
QFrame[dropzone="true"] {
    background-color: $window;
    border: 1px dashed $line_strong;
    border-radius: 7px;
}
QFrame[dropzone="true"][dragover="true"] {
    background-color: #102519;
    border: 1px dashed $green;
}

/* V2 home dashboard */
QFrame[homeAction="true"] {
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 10px;
}
QFrame[workflowCard="true"] {
    background-color: $window;
    border: 1px solid #34352e;
    border-radius: 10px;
}
QLabel[role="homeTitle"] {
    color: $text;
    font-size: 28px;
    font-weight: 700;
}
QLabel[role="homeBody"] {
    color: $muted_bright;
    font-size: 14px;
}
QLabel[role="homeSectionTitle"] {
    color: $text;
    font-size: 20px;
    font-weight: 750;
}
QLabel[role="workflowTitle"] {
    color: $text;
    font-size: 17px;
    font-weight: 750;
}
QFrame[workflowStep="true"] {
    background-color: $surface;
    border: 1px solid #34352e;
    border-radius: 7px;
}
QLabel[stepNumber="true"] {
    color: $green;
    background-color: #102519;
    border: 1px solid #2b6b43;
    border-radius: 15px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}
QLabel[role="stepTitle"] {
    color: $text;
    font-size: 13px;
    font-weight: 700;
}
QLabel[role="privacyNote"] {
    color: #70d997;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .5px;
    padding-top: 3px;
}
QFrame[homeDropzone="true"] {
    background-color: $window;
    border-color: $line_strong;
}
QLabel[role="dropIcon"] {
    color: $green;
    font-size: 25px;
    font-weight: 700;
}
QLabel[role="dropTitle"] {
    color: $text;
    font-size: 13px;
    font-weight: 700;
}
QLabel[role="emptyIcon"] {
    color: $green;
    font-size: 24px;
}
QLabel[role="localStatus"] {
    color: $green;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}
QFrame[projectCard="true"] {
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 10px;
}
QFrame[projectCard="true"]:hover {
    background-color: $raised;
    border-color: #686555;
}
QFrame[addProjectCard="true"] {
    background-color: $window;
    border: 1px dashed $line_strong;
    border-radius: 10px;
}
QFrame[addProjectCard="true"]:hover {
    background-color: $surface;
    border-color: $green;
}
QLabel[projectThumb="true"] {
    color: #647169;
    background-color: $canvas;
    border: 1px solid #34352e;
    border-radius: 3px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
}
QLabel[role="projectTitle"] {
    color: $text;
    font-size: 14px;
    font-weight: 750;
}
QLabel[role="projectMeta"] {
    color: #748078;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
}
QLabel[role="projectState"] {
    color: $muted;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
}
QPushButton[projectAction="true"] {
    color: #bff4d1;
    background-color: #102519;
    border-color: #2c6b44;
    padding: 4px 11px;
    min-height: 16px;
}
QPushButton[projectAction="true"]:hover {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    color: $text;
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 7px;
    padding: 6px 9px;
    selection-color: #061109;
    selection-background-color: $green;
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: #686555; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: $green;
    background-color: $raised;
}
/* Deliberately not styling ::drop-down or ::down-arrow. Qt stops drawing
   its own arrow as soon as either is customised, and border-drawn
   triangles render as filled rectangles rather than arrows. A field with a
   list behind it that looks exactly like a text box is worse than a
   slightly off-brand arrow, so the native one stays. */
QComboBox QAbstractItemView {
    color: $text;
    background-color: $raised;
    border: 1px solid $line_strong;
    selection-color: $green_dark;
    selection-background-color: $green;
}

QTableWidget, QTableView, QListWidget, QTreeWidget {
    color: $text;
    background-color: $window;
    alternate-background-color: $surface;
    border: 1px solid #34352e;
    border-radius: 7px;
    gridline-color: $hover;
}
QTableWidget::item, QTableView::item, QListWidget::item {
    border: none;
    padding: 5px;
}
QTableWidget::item:hover, QTableView::item:hover, QListWidget::item:hover {
    background-color: $hover;
}
QTableWidget::item:selected, QTableView::item:selected, QListWidget::item:selected {
    color: $text;
    background-color: $green_dim;
    border-left: 3px solid $green;
}
QHeaderView::section {
    color: $muted;
    background-color: $surface;
    border: none;
    border-right: 1px solid #34352e;
    border-bottom: 1px solid #424239;
    padding: 7px 9px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}

QSlider::groove:horizontal {
    height: 3px;
    background: #424239;
    border-radius: 3px;
}
QSlider::sub-page:horizontal { background: #686555; border-radius: 3px; }
/* A slim bar rather than a bordered disc. The old 15px green circle was the
   loudest thing in the transport row and read as unfinished Qt default. Six
   pixels stays comfortably grabbable with a mouse. */
QSlider::handle:horizontal {
    width: 6px;
    height: 14px;
    margin: -6px 0;
    background: $muted_bright;
    border: none;
    border-radius: 3px;
}
QSlider::handle:horizontal:hover { background: $green; }
QProgressBar {
    color: $muted_bright;
    background-color: $raised;
    border: 1px solid #424239;
    border-radius: 3px;
    text-align: center;
    height: 16px;
}
QProgressBar::chunk { background-color: $green; border-radius: 3px; }
QProgressBar[logging="true"] {
    background-color: $hover;
    border: none;
    border-radius: 3px;
    height: 5px;
}
QProgressBar[logging="true"]::chunk { background-color: $green; }

/* Export & Cutups replaces Tag Map as a complete lower-stage workspace. */
#V2ExportWorkspace {
    background-color: $window;
    border: none;
}
#ExportStageStrip,
#ExportSetupEditor,
#ExportSetupSummary,
#ExportFooter {
    background-color: $surface;
    border: 1px solid $line;
    border-radius: 8px;
}
QLabel[exportStage="true"] {
    min-height: 27px;
    color: $muted;
    background-color: $surface;
    border: none;
    border-bottom: 2px solid #424239;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 600;
}
QLabel[exportStage="true"][active="true"] {
    color: $green;
    background-color: #132019;
    border-bottom: 2px solid $green;
    font-weight: 700;
}
#ExportSetupStack {
    background-color: transparent;
    border: none;
}
#ExportSetupEditor QWidget,
#ExportSetupSummary QWidget {
    background-color: transparent;
}
#V2ExportWorkspace QToolButton[exportStyleCard="true"] {
    color: #aab4ad;
    background-color: #121612;
    border: 1px solid #354039;
    border-radius: 6px;
    padding: 7px 10px;
    text-align: left;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 650;
}
#V2ExportWorkspace QToolButton[exportStyleCard="true"]:hover {
    color: #e8eee9;
    border-color: #557060;
    background-color: #171d18;
}
#V2ExportWorkspace QToolButton[exportStyleCard="true"]:checked {
    color: #a0f1bb;
    background-color: #11251a;
    border: 1px solid $green;
}
#ExportPreviewCard {
    background-color: #0c100d;
    border: none;
    border-radius: 8px;
}
#ExportPackagePreview {
    color: #5f6c63;
    background-color: #060806;
    border: 1px solid #222a24;
    border-radius: 4px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#ExportPreviewStatus {
    color: #7f8d83;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#ExportCompositedUnavailable {
    color: #d3b75f;
    background-color: #201b0e;
    border: 1px solid #66501f;
    border-radius: 4px;
    padding: 4px 7px;
    font-size: 10px;
}
#ExportStart:disabled {
    color: #748078;
    background-color: #111511;
    border-color: #364039;
}
QLabel[role="exportCaption"] {
    color: #7f8d83;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
}
QLabel[role="exportSummary"] {
    color: #dce4de;
    font-size: 12px;
}
#ExportSummaryDivider {
    color: #424239;
    background-color: #424239;
    border: none;
    min-width: 1px;
    max-width: 1px;
}
#ExportDestination {
    color: $muted_bright;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
#ExportWarning {
    color: $warning;
    background-color: #211b0d;
    border: 1px solid #66501f;
    border-radius: 4px;
    padding: 4px 8px;
}
#ExportLane {
    background-color: $surface;
    border: 1px solid $line;
    border-radius: 8px;
}
#ExportLaneScroll,
#ExportLaneBody {
    background-color: transparent;
    border: none;
}
QLabel[role="exportLaneTitle"] {
    color: #dce4de;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: .6px;
}
QLabel[role="exportLaneCount"] {
    color: $green;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
QLabel[role="exportEmpty"] {
    color: #647169;
    border: 1px dashed #424239;
    border-radius: 4px;
    padding: 15px;
}
#ExportLane[allEmpty="true"] {
    background-color: $window;
}
#ExportLane[allEmpty="true"] QLabel[role="exportEmpty"] {
    color: $muted;
    background-color: $surface;
    border: none;
    border-radius: 6px;
    padding: 8px;
}
QFrame[exportJob="active"],
QFrame[exportJob="queued"],
QFrame[exportJob="complete"] {
    background-color: #151916;
    border: 1px solid #27322b;
    border-radius: 4px;
}
QFrame[exportJob="active"] {
    border-left: 3px solid $green;
}
QLabel[role="exportJobTitle"] {
    color: $text;
    font-size: 11px;
    font-weight: 650;
}
QLabel[role="exportJobMeta"] {
    color: $muted;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
QLabel[role="exportQueueIndex"] {
    min-width: 12px;
    color: $muted_bright;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
QLabel[role="exportPercent"] {
    color: #dce4de;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
    font-weight: 700;
}
QLabel[result="success"],
QLabel[result="error"] {
    min-width: 19px;
    max-width: 19px;
    min-height: 19px;
    max-height: 19px;
    border-radius: 9px;
    font-size: 13px;
    font-weight: 700;
}
QLabel[result="success"] {
    color: $green_dark;
    background-color: $green;
}
QLabel[result="error"] {
    color: #ffffff;
    background-color: #c94f45;
}
#ExportThumbnail {
    color: #647169;
    background-color: #090b0a;
    border: 1px solid #424239;
    border-radius: 3px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 8px;
    font-weight: 700;
}
#ExportJobProgress,
#ExportOverallProgress {
    height: 8px;
    max-height: 8px;
    background-color: #232924;
    border: none;
    border-radius: 2px;
}
#ExportJobProgress::chunk,
#ExportOverallProgress::chunk {
    background-color: $green;
    border-radius: 2px;
}
QLabel[role="exportOverall"] {
    min-width: 180px;
    color: $muted_bright;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
#V2ExportWorkspace QPushButton {
    padding: 4px 10px;
    min-height: 18px;
    border-radius: 4px;
    font-size: 10px;
}
#V2ExportWorkspace QComboBox {
    padding: 4px 7px;
    min-height: 20px;
    border-radius: 4px;
}

QWidget#CenteredApplicationMenu {
    background-color: $canvas;
    border-bottom: 1px solid #34352e;
}
QMenuBar#CenteredApplicationMenuBar {
    color: $muted_bright;
    background-color: transparent;
    border: none;
    padding: 3px;
}
QMenuBar#CenteredApplicationMenuBar::item {
    padding: 6px 11px;
    border-radius: 3px;
}
QMenuBar#CenteredApplicationMenuBar::item:selected {
    color: #ffffff;
    background-color: $hover;
}
QMenu {
    color: #dce4de;
    background-color: $surface;
    border: 1px solid $line_strong;
    padding: 5px;
}
QMenu::item { padding: 7px 26px 7px 10px; border-radius: 3px; }
QMenu::item:selected { color: $green_dark; background-color: $green; }
QMenu::separator { height: 1px; background-color: #424239; margin: 5px 7px; }

QGroupBox {
    color: #dce4de;
    border: 1px solid #424239;
    border-radius: 7px;
    margin-top: 12px;
    padding-top: 13px;
    font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 5px; }
QTabWidget::pane { border: 1px solid #424239; border-radius: 7px; }
QTabBar::tab {
    color: $muted;
    background: $surface;
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: $green; border-bottom-color: $green; }
QSplitter::handle { background-color: $hover; }
QSplitter::handle:hover { background-color: $line_strong; }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }

QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: $window; width: 10px; margin: 0; }
QScrollBar::handle:vertical {
    background: $line_strong;
    border-radius: 3px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #686555; }
QScrollBar:horizontal { background: $window; height: 10px; }
QScrollBar::handle:horizontal { background: $line_strong; border-radius: 3px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }

QStatusBar {
    color: #748078;
    background-color: $canvas;
    border-top: 1px solid #34352e;
}
QToolTip {
    color: $text;
    background-color: $hover;
    border: 1px solid #686555;
    padding: 5px;
}
QMessageBox {
    background-color: $canvas;
}
QMessageBox QLabel#qt_msgbox_label {
    color: $text;
    font-size: 15px;
    font-weight: 700;
    min-width: 380px;
}
QMessageBox QLabel#qt_msgbox_informativelabel {
    color: $muted;
    min-width: 380px;
}
QMessageBox QPushButton { min-width: 88px; }

/* Film Room headline. Rajdhani (the display family used by the wordmark and
   eyebrows) so it belongs to the same type system instead of falling back to
   proportional Segoe UI, which read as pasted in. */
#V2StartScreen {
    background: qradialgradient(
        cx: 0.16, cy: 0.08, radius: 1.15,
        fx: 0.16, fy: 0.08,
        stop: 0 #20211d,
        stop: 0.52 $canvas,
        stop: 1 #121310
    );
}
#V2StartScreen QLabel[role="roomTitle"] {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 28px;
    font-weight: 600;
    letter-spacing: .3px;
}

#V2StartScreen QFrame[homeDropzone="true"] {
    background-color: $window;
    border: 1px dashed $line_strong;
    border-radius: 7px;
}
#V2StartScreen QFrame[homeDropzone="true"][dragover="true"] {
    background-color: #102519;
    border-color: $green;
}
#V2StartScreen QLabel[role="dropHeading"] {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 21px;
    font-weight: 700;
    letter-spacing: .7px;
}
#V2StartScreen QFrame[workflowConnector="true"] {
    background-color: #28563a;
    border: none;
}
#V2StartScreen QFrame[homeRule="true"] {
    background-color: #34352e;
    border: none;
}
#V2StartScreen QLabel[workflowNumber="true"] {
    color: $green;
    background-color: $window;
    border: 1px solid #748078;
    border-radius: 24px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 18px;
    font-weight: 700;
}
#V2StartScreen QLabel[role="workflowHeading"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: .5px;
}
#V2StartScreen QLabel[role="queueHeading"] {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 21px;
    font-weight: 700;
    letter-spacing: .7px;
}
#V2StartScreen QFrame[projectCard="true"] {
    background-color: transparent;
    border: none;
    border-top: 1px solid #253029;
    border-radius: 0;
}
#V2StartScreen QFrame[projectCard="true"]:hover {
    background-color: $surface;
    border-top-color: #686555;
}
#V2StartScreen QLabel[projectThumb="true"] {
    color: #647169;
    background-color: $canvas;
    border: 1px solid #34352e;
    border-radius: 3px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
}
#V2StartScreen QLabel[role="projectTitle"] {
    color: $text;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 19px;
    font-weight: 700;
}
#V2StartScreen QLabel[role="projectSource"] {
    color: $muted;
    font-size: 13px;
}
#V2StartScreen QLabel[role="projectMeta"] {
    color: $muted;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .4px;
}
#V2StartScreen QLabel[role="metricValue"],
#V2StartScreen QLabel[role="metricAccent"] {
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 23px;
    font-weight: 600;
}
#V2StartScreen QLabel[role="metricValue"] { color: $text; }
#V2StartScreen QLabel[role="metricAccent"] { color: $green; }
#V2StartScreen QLabel[role="metricLabel"] {
    color: #748078;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .3px;
}
#V2StartScreen QPushButton[projectAction="true"] {
    color: $green;
    background-color: transparent;
    border-color: #2b7a49;
    min-height: 24px;
}
#V2StartScreen QPushButton[projectAction="true"]:hover {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
#V2StartScreen QToolButton[projectMenu="true"] {
    min-width: 24px;
    padding: 7px;
    background-color: $surface;
}
#V2StartScreen QFrame[addProjectRow="true"] {
    background-color: transparent;
    border: 1px dashed #424239;
    border-radius: 3px;
}
#V2StartScreen QFrame[addProjectRow="true"]:hover {
    background-color: $window;
    border-color: $line_strong;
}
#V2StartScreen QLabel[role="addMark"] {
    color: #dce4de;
    background-color: $window;
    border: 1px dashed $line_strong;
    border-radius: 3px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-weight: 600;
    font-size: 30px;
}
#V2StartScreen QPushButton#HomeNewProject {
    min-width: 180px;
    min-height: 28px;
}
#V2StartScreen QPushButton#HomeOpenProject {
    color: $muted_bright;
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    min-width: 220px;
    max-width: 220px;
    min-height: 20px;
    padding: 6px 9px;
    text-align: left;
}
#V2StartScreen QPushButton#HomeOpenProject:hover {
    color: $text;
    background-color: $surface;
}
#V2StartScreen QPushButton#HomeOpenProject:focus {
    color: $text;
    border-color: $green;
}
#V2StartScreen QPushButton[homeSocialLink="true"] {
    color: $green;
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    min-height: 20px;
    padding: 2px 5px;
    font-weight: 600;
}
#V2StartScreen QPushButton[homeSocialLink="true"]:hover {
    color: $green_hover;
    background-color: $surface;
}
#V2StartScreen QPushButton[homeSocialLink="true"]:focus {
    border-color: $green;
}
#V2StartScreen QLabel[role="footerSeparator"] {
    color: #686555;
}

/* Library: selected-play workbench and scouting ledger */
#V2LibraryScreen {
    background: qradialgradient(
        cx: 0.18, cy: 0.06, radius: 1.20,
        fx: 0.18, fy: 0.06,
        stop: 0 #20211d,
        stop: 0.52 $canvas,
        stop: 1 #121310
    );
}
#V2LibraryScreen QLabel[role="libraryStats"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .8px;
}
#V2LibraryScreen QLabel[role="librarySection"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: .8px;
}
#V2LibraryScreen QLabel[role="libraryHint"] {
    color: #647169;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .45px;
}
#V2LibraryScreen QLineEdit#V2LibrarySearch {
    color: $text;
    background-color: $window;
    border: 1px solid #2f8d52;
    border-radius: 3px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 600;
    padding-left: 11px;
}
#V2LibraryScreen QLineEdit#V2LibrarySearch:focus {
    background-color: $raised;
    border-color: $green;
}
#V2LibraryScreen QComboBox[libraryFilter="true"],
#V2LibraryScreen QToolButton[libraryFilter="true"] {
    color: #dce4de;
    background-color: $surface;
    border: 1px solid #424239;
    border-radius: 3px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 600;
    padding: 5px 9px;
}
#V2LibraryScreen QComboBox[libraryFilter="true"]:hover,
#V2LibraryScreen QToolButton[libraryFilter="true"]:hover {
    color: $text;
    border-color: #686555;
}
#V2LibraryScreen QFrame#V2LibraryWorkbench {
    background-color: $canvas;
    border: 1px solid #424239;
    border-radius: 3px;
}
#V2LibraryScreen QFrame#V2LibraryFilmPanel {
    background-color: $canvas;
    border: none;
}
#V2LibraryScreen QWidget#V2LibraryInspector {
    background-color: $window;
    border-left: 1px solid #424239;
}
#V2LibraryScreen QFrame#V2LibraryLedgerHeader {
    background-color: $window;
    border: 1px solid #424239;
    border-bottom: none;
    border-radius: 0;
}
#V2LibraryScreen QLabel[role="ledgerColumn"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .55px;
}
#V2LibraryScreen QLabel[role="librarySubheading"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: .6px;
}
#V2LibraryScreen QLabel[role="libraryFieldLabel"] {
    color: $muted;
    font-family: "Segoe UI", sans-serif;
    font-size: 10px;
    font-weight: 600;
}
#V2LibraryScreen QListWidget#V2LibraryResults {
    background-color: $window;
    alternate-background-color: $surface;
    border: 1px solid #34352e;
    border-radius: 0;
    outline: none;
}
#V2LibraryScreen QListWidget#V2LibraryResults::item {
    border: none;
    padding: 0;
}
#V2LibraryScreen QListWidget#V2LibraryResults::item:hover {
    background-color: $raised;
}
#V2LibraryScreen QListWidget#V2LibraryResults::item:selected {
    background-color: #164d2c;
    border: none;
}
#V2LibraryScreen QLabel[role="libraryCount"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: .35px;
}
#V2LibraryScreen QLabel[role="libraryInspectorHeading"] {
    color: $green;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: .8px;
}
#V2LibraryScreen QLabel#V2LibraryPreview {
    color: #647169;
    background-color: $canvas;
    border: 1px solid #424239;
    border-radius: 3px;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 13px;
    font-weight: 600;
}
#V2LibraryScreen QLineEdit#V2LibraryClipTitle {
    color: $text;
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #424239;
    border-radius: 0;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 28px;
    font-weight: 700;
    padding: 1px 0;
}
#V2LibraryScreen QLabel[role="libraryMeta"] {
    color: $muted;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 600;
}
#V2LibraryScreen QWidget#V2LibraryInspector QLineEdit,
#V2LibraryScreen QWidget#V2LibraryInspector QPlainTextEdit,
#V2LibraryScreen QWidget#V2LibraryInspector QComboBox {
    border-radius: 3px;
    padding: 3px 13px;
    min-height: 20px;
}
#V2LibraryScreen QFrame#LibraryNotesResizeHandle {
    background-color: #424239;
    border: none;
    border-radius: 2px;
}
#V2LibraryScreen QFrame#LibraryNotesResizeHandle:hover {
    background-color: $green;
}
#V2LibraryScreen QPushButton[projectAction="true"] {
    color: $green;
    background-color: transparent;
    border-color: #2b7a49;
    border-radius: 3px;
}
#V2LibraryScreen QToolButton#LibraryPreviewRewind,
#V2LibraryScreen QToolButton#LibraryPreviewPlayPause,
#V2LibraryScreen QToolButton#LibraryPreviewFastForward {
    background-color: #20211d;
    border: 1px solid #44443b;
    border-radius: 4px;
    padding: 3px;
}
#V2LibraryScreen QToolButton#LibraryPreviewRewind:hover,
#V2LibraryScreen QToolButton#LibraryPreviewPlayPause:hover,
#V2LibraryScreen QToolButton#LibraryPreviewFastForward:hover {
    background-color: #313129;
    border-color: #85806b;
}
#V2LibraryScreen QToolButton#LibraryPreviewPlayPause[playing="true"] {
    border-color: $green;
}
#V2LibraryScreen QToolButton#LibraryPreviewRewind:disabled,
#V2LibraryScreen QToolButton#LibraryPreviewPlayPause:disabled,
#V2LibraryScreen QToolButton#LibraryPreviewFastForward:disabled {
    background-color: #18231b;
    border-color: #2b3d30;
}
#V2LibraryScreen QPushButton[projectAction="true"]:hover {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
/* ====== iteration one: quick tags, trays, popups ====== */

QWidget#CenteredApplicationMenu {
    /* A hair lighter than the canvas and falling off toward the content:
       the bar reads as a raised top surface, not a flat strip. */
    background-color: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1f201b, stop:1 #151613);
    border-bottom: 1px solid $line;
}
QWidget#V2ReviewMasthead,
QWidget#HomeApplicationMasthead,
QWidget#LibraryApplicationMasthead,
QWidget#ApplicationTitleBarRight,
QWidget#ReviewMastheadActions,
QWidget#HomeMastheadActions,
QWidget#LibraryMastheadActions,
QWidget#ApplicationWindowControls {
    background-color: transparent;
}
QMenuBar#CenteredApplicationMenuBar {
    padding: 0;
}
QMenuBar#CenteredApplicationMenuBar::item {
    padding: 7px 10px;
}
QToolButton#ReviewProjectButton {
    /* The project name is a chip: a surface the user can press, not bare
       text that happens to open a menu. */
    color: $text;
    background-color: $surface;
    border: 1px solid $line;
    border-radius: 5px;
    padding: 5px 10px;
    text-align: left;
    font-size: 12px;
}
QToolButton#ReviewProjectButton:hover {
    background-color: $raised;
    border-color: $line_strong;
}
QToolButton#ReviewProjectButton:pressed {
    background-color: $hover;
}
QPushButton#ReviewPopOutButton {
    color: #e6bd5d;
    background-color: transparent;
    border-color: #665627;
    border-radius: 3px;
    min-height: 23px;
    padding: 3px 12px;
}
QPushButton#ReviewPopOutButton:hover {
    color: #fff0be;
    background-color: #2e2817;
    border-color: #b6933d;
}
QToolButton[windowControl="true"] {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 0;
}
QToolButton[windowControl="true"]:hover {
    background-color: #2b2b25;
    border: none;
    border-radius: 4px;
}
QToolButton#ApplicationCloseButton:hover {
    background-color: #b13a3a;
}
QFrame#HomeBrandDivider,
QFrame#LibraryBrandDivider,
QFrame#ReviewBrandDivider {
    background-color: $line_strong;
    border: none;
}
QPushButton[libraryEntry="true"] {
    min-height: 28px;
    max-height: 32px;
    padding: 3px 11px;
    border-radius: 4px;
}
QPushButton#ApplicationSettingsButton {
    min-height: 28px;
    max-height: 32px;
}
QLabel#HomeSectionLabel,
QLabel#LibrarySectionLabel {
    color: $muted_bright;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#HomeWelcomeTitle {
    font-size: 30px;
    font-weight: 700;
}
#V2StartScreen QLabel[role="roomSubtitle"] {
    color: $muted_bright;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 21px;
    font-weight: 600;
}
QLabel#HomeBuildLabel,
QLabel#LibraryMastheadStats {
    color: $muted;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 10px;
}
QToolButton[mediaControl="true"] {
    background-color: #20211d;
    border: 1px solid #44443b;
    border-radius: 17px;
    padding: 3px;
}
QToolButton[mediaControl="true"]:hover {
    background-color: #313129;
    border-color: #85806b;
}
QToolButton[mediaControl="true"]:pressed {
    background-color: #151713;
    border-color: $green;
}
QToolButton[circularControl="true"] {
    background-color: #20211d;
    border: 1px solid #44443b;
    border-radius: 16px;
    padding: 0;
}
QToolButton[circularControl="true"]:hover {
    background-color: #313129;
    border-color: #85806b;
}
QToolButton[circularControl="true"]:pressed {
    background-color: #151713;
    border-color: $green;
}
QWidget#TimelineViewControls,
QWidget#TransportCluster,
QWidget#TransportRightGroup {
    background-color: transparent;
}
QSlider#InlineVolumeSlider::groove:horizontal {
    height: 3px;
    background-color: #414138;
    border-radius: 1px;
}
QSlider#InlineVolumeSlider::sub-page:horizontal {
    background-color: $muted;
}
QSlider#InlineVolumeSlider::handle:horizontal {
    width: 9px;
    margin: -4px 0;
    background-color: $muted_bright;
    border: 1px solid #6d6959;
    border-radius: 5px;
}
QToolBar#V2ReviewMasthead {
    background-color: $window;
    border: none;
    border-bottom: 1px solid $line;
    spacing: 8px;
    padding: 5px 14px;
}
QToolBar#V2ReviewMasthead QWidget {
    background-color: transparent;
}
QLabel#ReviewProjectProgressLabel,
QLabel#ReviewAutosaveLabel {
    color: $muted;
    font-size: 11px;
}
QLabel#ReviewAutosaveDot {
    /* The live-status speck beside Autosaved: saved state is a status,
       and statuses read better as a light than as more text. */
    background-color: $green;
    border-radius: 3px;
}
QProgressBar#ReviewProjectProgress {
    min-width: 150px;
    max-width: 220px;
    min-height: 4px;
    max-height: 4px;
    background-color: #34352e;
    border: none;
    border-radius: 2px;
    text-align: center;
}
QProgressBar#ReviewProjectProgress::chunk {
    background-color: #77705a;
    border-radius: 2px;
}
#InspectorReviewState[state="logged"],
#InspectorReviewState[state="unlogged"],
#InspectorSaveState[state="saved"],
#InspectorSaveState[state="dirty"] {
    background-color: transparent;
    border-radius: 3px;
    padding: 2px 5px;
    font-size: 9px;
}
#V2QuickTagTray {
    border-radius: 4px;
}
/* Restated in this sheet too: it is appended after the base one, and the
   inline rail must not pick the card back up here. */
#V2QuickTagTray[inline="true"] {
    background: transparent;
    border: none;
    border-radius: 0px;
}
/* The overflow is a panel of the same buttons, so it keeps the card look
   the inline rail gives up. */
#V2QuickTagMorePopup {
    background-color: #12160f;
    border: 1px solid #3e4038;
    border-radius: 10px;
}
#V2QuickTagMorePopup QLabel[role="eyebrow"] {
    color: #7d857e;
    font-size: 11px;
    font-weight: 700;
    padding: 0px 0px 2px 2px;
}
#V2QuickTagMorePopup QToolButton[quickTagAdd="true"] {
    color: #8b968d;
    background: transparent;
    border: 1px dashed #4a4d42;
    border-radius: 4px;
    font-size: 17px;
    font-weight: 700;
    padding: 0px;
}
#V2QuickTagMorePopup QToolButton[quickTagAdd="true"]:hover {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
    border-style: solid;
}
#V2QuickTagMorePopup QPushButton[quickTag="true"] {
    min-height: 32px;
    padding: 7px 13px;
    border-radius: 4px;
    color: $muted_bright;
    background-color: $surface;
    border: 1px solid #424239;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 700;
}
#V2QuickTagMorePopup QPushButton[quickTag="true"]:hover {
    color: #ffffff;
    border-color: $green;
}
#V2QuickTagMorePopup QPushButton[quickTag="true"]:checked {
    color: $green_dark;
    background-color: $green;
    border-color: $green;
}
#V2QuickTagTray QLabel[role="eyebrow"] {
    padding-right: 3px;
    font-size: 11px;
}
#V2QuickTagTray QPushButton[quickTag="true"] {
    min-height: 36px;
    max-height: 36px;
    padding: 5px 10px;
    border-radius: 5px;
    font-size: 13px;
}
#V2QuickTagTray QPushButton[quickTagControl="true"],
#V2QuickTagTray QToolButton[quickTagControl="true"] {
    min-height: 36px;
    max-height: 36px;
    padding: 5px 11px;
    border-radius: 5px;
    font-size: 13px;
}
#V2QuickTagTray QToolButton#V2QuickTagAddButton {
    min-width: 32px;
}
/* ====== finish pass: pressed states, window radius ====== */

/* Window controls: pressed feedback to match the existing hover language. */
QToolButton[windowControl="true"]:pressed {
    background-color: $window;
}
QToolButton#ApplicationCloseButton:pressed {
    background-color: #8f2e2e;
}
/* Filled action pressed: darkens instead of inverting. */
QPushButton[primary="true"]:pressed, QToolButton[primary="true"]:pressed,
QPushButton[accent="true"]:pressed, QToolButton[accent="true"]:pressed {
    background-color: #2fbc66;
    border-color: #2fbc66;
    color: #061109;
}
/* Hero/statement band: hairline one step stronger for presence. */
QWidget[hero="true"] {
    border-color: #424239;
}
/* Rounded application window. DWM corner rounding is not guaranteed on
   every Windows install, so the frameless shell paints its own radius.
   The opaque base is painted in MainWindowV2.paintEvent (QSS background
   colors are ignored on the translucent top-level window itself); these
   rules give the masthead (top corners) and the status bar (bottom
   corners) the matching radius so they do not cover the base's curves.
   The radius is unconditional: it does not depend on window state, so a
   missed state sync can never leave hard square corners on screen. */
QWidget#CenteredApplicationMenu {
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}
#TapeSiftV2 QStatusBar {
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
}

/* Inspector empty state: the biggest blank surface in the app gets a
   designed panel - a tinted icon disc, a plain-language line, and the
   three keys that fill it, as keyboard chips. */
#ClipEditorEmptyState {
    background: transparent;
}
#ClipEditorEmptyState QLabel#ClipEditorEmptyIcon {
    color: $green;
    background-color: rgba(57, 224, 122, 0.13);
    border-radius: 30px;
    font-size: 24px;
}
#ClipEditorEmptyState QLabel#ClipEditorEmptyKey {
    color: $muted_bright;
    background-color: $raised;
    border: 1px solid $line;
    border-bottom-width: 2px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 600;
}
#ClipEditorEmptyState QLabel#ClipEditorEmptyHint {
    color: $muted;
    font-size: 12px;
}

/* Iteration 2A: Option 4 workflow surfaces. The time-aligned grid remains the
   live editor; this pass only sharpens its graphite container and gives each
   quick-tag family its own stable scan colour. */
#TagMapHeader {
    background-color: #141713;
    border-color: #33372f;
}
#TagMapCollapse {
    min-height: 24px;
    max-height: 24px;
    padding: 0 9px;
    border-radius: 3px;
}
#V2QuickTagTray[inline="true"] QPushButton[quickTag="true"] {
    min-height: 24px;
    max-height: 24px;
    padding: 1px 8px;
    border-radius: 3px;
    background-color: #181b17;
}
#V2QuickTagTray[inline="true"] QPushButton[quickTagControl="true"],
#V2QuickTagTray[inline="true"] QToolButton[quickTagControl="true"] {
    min-height: 24px;
    max-height: 24px;
    padding: 1px 8px;
    border-radius: 3px;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="run"] {
    color: #8be5ad; border-color: #315f43; background-color: #15231a;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="pass"] {
    color: #86bdff; border-color: #315b85; background-color: #15202b;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="screen"] {
    color: #bd9aef; border-color: #5b4778; background-color: #211a2b;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="rpo"] {
    color: #74e4d5; border-color: #2d6f67; background-color: #142725;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="penalty"] {
    color: #e8cd6a; border-color: #756529; background-color: #292410;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="sack"] {
    color: #f3b35a; border-color: #75501f; background-color: #291e11;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="interception"] {
    color: #ff8b9b; border-color: #7a3440; background-color: #2a1519;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="touchdown"] {
    color: #d3a5ff; border-color: #68478a; background-color: #25192e;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="firstDown"] {
    color: #8be5ad; border-color: #315f43; background-color: #15231a;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="fumble"] {
    color: #ff9c77; border-color: #7a4632; background-color: #2a1912;
}
#V2QuickTagTray QPushButton[quickTag="true"]:checked {
    color: #07110b;
    font-weight: 700;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="run"]:checked,
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="firstDown"]:checked {
    background-color: #57c98a; border-color: #8be5ad;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="pass"]:checked {
    background-color: #4da3ff; border-color: #86bdff;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="screen"]:checked {
    background-color: #9567d8; border-color: #bd9aef;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="rpo"]:checked {
    background-color: #36d6c0; border-color: #74e4d5;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="penalty"]:checked {
    background-color: #d9b43b; border-color: #e8cd6a;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="sack"]:checked {
    background-color: #f59e0b; border-color: #f3b35a;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="interception"]:checked {
    background-color: #ff5d73; border-color: #ff8b9b;
}
#V2QuickTagTray QPushButton[quickTag="true"][tagFamily="touchdown"]:checked {
    background-color: #c084fc; border-color: #d3a5ff;
}

#V2ReviewClipList QTableWidget {
    background-color: #151713;
    alternate-background-color: #151713;
    border-radius: 0px;
    gridline-color: transparent;
}
#V2ReviewClipList QTableWidget::item {
    border-bottom: 1px solid #30332c;
    padding: 2px 5px;
}
#V2ReviewClipList QTableWidget::item:selected {
    color: #f0f5f1;
    background-color: #173722;
    border-top: 1px solid #4a9a68;
    border-bottom: 1px solid #4a9a68;
}
#ClipLedgerHeaderActions {
    background-color: #171915;
    border-bottom: 1px solid #30332c;
    padding-bottom: 4px;
}

#V2ReviewInspector #InspectorSummaryCard {
    border-bottom: 1px solid #3a3e35;
}
#V2ReviewInspector #FirstReadAdvisory {
    background-color: #171a16;
    border: 1px solid #41453b;
    border-left: 2px solid #77705a;
    border-radius: 4px;
}
#V2ReviewInspector #FirstReadTitle {
    color: #9aa49c;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
}
#V2ReviewInspector #FirstReadSuggestionChip {
    color: #e9f2eb;
    background-color: #17251c;
    border: 1px solid #3d7650;
    border-radius: 3px;
    padding: 2px 7px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 9px;
    font-weight: 700;
}
#V2ReviewInspector #FirstReadMessage {
    color: #aab2ab;
    font-size: 10px;
}
#V2ReviewInspector #FirstReadAdvisory[state="disagreed"] {
    border-left-color: #c9b66e;
}
#V2ReviewInspector #FirstReadAdvisory[state="disagreed"] #FirstReadMessage {
    color: #d8c985;
}
#V2ReviewInspector #FirstReadAdvisory[state="frames_failed"] {
    border-left-color: #b87368;
}
#V2ReviewInspector #FirstReadAdvisory[state="frames_failed"] #FirstReadMessage {
    color: #dda096;
}
#FirstReadSecurityPromise {
    color: #c7d2c9;
    background-color: #141d17;
    border: 1px solid #315c43;
    border-radius: 4px;
    padding: 9px;
}
#V2ReviewInspector QWidget#InspectorPrimaryFields[analystRange="true"] {
    background-color: #111914;
    border-color: #2f8650;
}
#InspectorActionBar {
    background-color: #11130f;
    border-top: 1px solid #3a3e35;
}
#InspectorActionBar QPushButton#InspectorPackageExport {
    color: #dce4de;
    background-color: #191b17;
    border: 1px solid #4a4e44;
    font-family: Rajdhani, "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 700;
}
#InspectorActionBar QPushButton#InspectorPackageExport:hover {
    color: #ffffff;
    background-color: #22251f;
    border-color: #6c7163;
}
#InspectorActionBar QPushButton#InspectorQuickExport {
    color: #62dc91;
    background-color: #111a15;
    border: 1px solid #315c43;
}
#InspectorActionBar QPushButton#InspectorQuickExport:disabled {
    color: #647169;
    background-color: #151713;
    border-color: #30352f;
}
#InspectorActionBar QPushButton[quiet="true"] {
    color: #aab2ab;
    background-color: transparent;
    border-color: transparent;
}
#InspectorActionBar QPushButton#InspectorSaveNext {
    min-height: 26px;
}
"""


def stylesheet() -> str:
    return Template(V2_QSS).substitute(COLORS)
