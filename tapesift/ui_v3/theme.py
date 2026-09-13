"""Shell V3 additions layered over TapeSift's existing design system."""

from __future__ import annotations

from tapesift.ui_v2.theme import stylesheet as v2_stylesheet
from tapesift.ui_v3.icons import icon_path


V3_REVIEW_CONTROL_CENTER_MATERIAL = r"""
QWidget#TransportPill, QWidget#ControlCenterDeckSurface, QWidget#DockV2Surface {
    background: #080c0a; border: none; border-top: 1px solid #202822;
    border-bottom: 1px solid #151d17; border-radius: 0;
}
QWidget#ControlCenterTopRow, QWidget#ControlCenterBottomRow,
QWidget#TransportPositionZone, QWidget#TransportMarksGroup,
QWidget#V3TimelineZoomCluster, QWidget#V3TransportUtilityRows {
    background: transparent; border: none;
}
QFrame#TransportZoneRule, QFrame#TransportRowRule { background: #202822; border: none; }
QLabel#DockV2Timecode { color: #f0b347; font-size: 12px; }
QLabel#DockV2FrameCounter { color: #dca23e; font-size: 10px; }
QWidget#TransportMarksGroup QPushButton[transport="true"],
QWidget#TransportExportZone QPushButton#PredictedSnapAction, QPushButton#TransportJogToggle,
QToolButton#DockV2Overflow, QWidget#V3TimelineZoomCluster QToolButton {
    color: #e8e5db; background: transparent; border: 1px solid transparent;
    border-radius: 2px; padding: 0;
    font-family: "IBM Plex Sans", "Segoe UI"; font-size: 11px; font-weight: 500;
}
QWidget#TransportExportZone QPushButton#PredictedSnapAction:hover, QPushButton#TransportJogToggle:hover,
QToolButton#DockV2Overflow:hover, QWidget#V3TimelineZoomCluster QToolButton:hover {
    background: #1c271f; color: #ffffff;
}
QWidget#TransportExportZone QPushButton#PredictedSnapAction:pressed, QPushButton#TransportJogToggle:pressed,
QToolButton#DockV2Overflow:pressed, QWidget#V3TimelineZoomCluster QToolButton:pressed {
    background: #25332a; color: #f0b347;
}
QWidget#TransportExportZone QPushButton#PredictedSnapAction:focus, QPushButton#TransportJogToggle:focus,
QToolButton#DockV2Overflow:focus, QWidget#V3TimelineZoomCluster QToolButton:focus {
    border-color: #efb047;
}
QWidget#TransportExportZone QPushButton#PredictedSnapAction:disabled, QPushButton#TransportJogToggle:disabled,
QToolButton#DockV2Overflow:disabled, QWidget#V3TimelineZoomCluster QToolButton:disabled { color: #647067; }
QPushButton#TransportJogToggle:checked { color: #f0b347; background: #202820; }
QWidget#TransportExportZone QPushButton#PredictedSnapAction, QPushButton#TransportJogToggle,
QToolButton#DockV2Overflow, QWidget#V3TimelineZoomCluster QToolButton {
    background:#080c0a; border:1px solid #4b574e; border-radius:3px;
    font-size:12px;
}
QLabel#V3TimelineZoomValue { color: #dedfe0; background:#080c0a;
    border:1px solid #4b574e; border-radius:3px; font:12px "IBM Plex Sans"; }
QWidget#ControlCenterTopRow QToolButton, QWidget#ControlCenterTopRow QPushButton#PredictedSnapAction {
    background:#141c21; color:#e4e8eb; border:1px solid #394650;
    border-radius:2px; padding:0; min-width:0; min-height:0;
    font:11px "Segoe UI";
}
QWidget#ControlCenterTopRow QToolButton:hover, QWidget#ControlCenterTopRow QPushButton#PredictedSnapAction:hover {
    background:#252e34; border-color:#65737d;
}
QWidget#ControlCenterTopRow QToolButton:focus {border-color:#d8e1e8;}
QWidget#ControlCenterTopRow QToolButton::menu-indicator {image:none;}
QLabel#V3TimelineZoomValue {background:#141c21; border-color:#394650; font:11px "Segoe UI";}
"""


V3_ANODIZED_FIELD_CONSOLE_MATERIAL = r"""
/* Anodized Field Console: selected Review material carried across the other
   shipped V3 surfaces. Paint only; geometry and behavior stay authoritative. */
QWidget#TapeSiftV3ApplicationBar[pageMode="home"],
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #151614,
        stop:0.14 #121311,
        stop:0.78 #0e0f0e,
        stop:1 #090a09);
    border: none;
    border-bottom: 1px solid #050505;
}

#V2StartScreen[shellV3Home="true"] {
    background: qradialgradient(
        cx:0.52, cy:0.40, radius:0.98,
        fx:0.52, fy:0.40,
        stop:0 #070807,
        stop:0.56 #0a0b0a,
        stop:1 #0e0f0e);
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunLeft {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #131412,
        stop:0.66 #10110f,
        stop:1 rgba(12, 13, 12, 0));
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunDropHost {
    background: #0b0c0b;
    border: none;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunActions,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeWorkflow {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"],
#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"] {
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"]:hover,
#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"]:hover {
    background: #101511;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"]:focus {
    background: #101511;
    border: 1px solid #286642;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QLabel[projectThumb="true"] {
    background: #070807;
    border: 1px solid #050505;
    border-top-color: #1d1e1c;
    border-radius: 3px;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone {
    background: qradialgradient(
        cx:0.50, cy:0.46, radius:0.78,
        fx:0.50, fy:0.46,
        stop:0 #111210,
        stop:0.58 #0e0f0e,
        stop:1 #0b0c0b);
    border: 1px dashed #292a27;
    border-radius: 3px;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone[dragover="true"] {
    background: qradialgradient(
        cx:0.50, cy:0.46, radius:0.78,
        fx:0.50, fy:0.46,
        stop:0 #17241a,
        stop:1 #0b0e0c);
    border: 1px solid #39e07a;
}

#V2LibraryScreen[shellV3Library="true"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #0e0f0e,
        stop:0.54 #0b0c0b,
        stop:1 #080908);
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryWorkbench {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #141513,
        stop:0.10 #10110f,
        stop:1 #090a09);
    border: none;
    border-top: 1px solid #20211f;
    border-bottom: 1px solid #050505;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QWidget#V3LibraryMainSurface {
    background: #090a09;
    border: none;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryFilmPanel,
#V2LibraryScreen[shellV3Library="true"] QStackedWidget#V2LibraryPreview,
#V2LibraryScreen[shellV3Library="true"] QLabel#V2LibraryPreview,
#V2LibraryScreen[shellV3Library="true"] QWidget#V3LibraryEmptyPreview {
    background: #070807;
    border: none;
    border-bottom: 1px solid #030303;
    border-radius: 2px;
}

#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #151614,
        stop:0.10 #121311,
        stop:1 #0b0c0b);
    border: none;
    border-left: 1px solid #050505;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryLedgerHeader {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #141513,
        stop:1 #0b0c0b);
    border: none;
    border-top: 1px solid #20211f;
    border-bottom: 1px solid #050505;
}

#V2LibraryScreen[shellV3Library="true"] QListWidget#V2LibraryResults {
    background: #0a0b0a;
    alternate-background-color: #0d0e0d;
    border: none;
    border-radius: 0px 0px 3px 3px;
    outline: none;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V3LibraryLedgerEmpty {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #131412,
        stop:1 #090a09);
    border: none;
    border-top: 1px solid #20211f;
    border-bottom: 1px solid #050505;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch,
#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibraryClipTitle,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QLineEdit,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QPlainTextEdit,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QComboBox,
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryEmptyRebuild,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBuildReel,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryExport,
#V2LibraryScreen[shellV3Library="true"] QPushButton[projectAction="true"],
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton#V3LibraryRebuild,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton#V3LibraryBack,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QToolButton#V3LibraryHome {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a1b19,
        stop:0.12 #171816,
        stop:0.78 #121312,
        stop:1 #0c0d0c);
    border: 1px solid #060706;
    border-top: 1px solid #292a27;
    border-bottom: 1px solid #030403;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch:focus,
#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibraryClipTitle:focus,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QLineEdit:focus,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QPlainTextEdit:focus,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QComboBox:focus,
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"]:hover,
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"]:focus,
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"]:hover,
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"]:focus,
#V2LibraryScreen[shellV3Library="true"] QPushButton:hover,
#V2LibraryScreen[shellV3Library="true"] QToolButton:hover,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton:hover,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QToolButton:hover {
    background: #1d1e1b;
    border-top-color: #343531;
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch:focus,
#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibraryClipTitle:focus,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QLineEdit:focus,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QPlainTextEdit:focus,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QComboBox:focus,
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"]:focus,
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"]:focus {
    border-left-color: #39e07a;
}

#V2LibraryScreen[shellV3Library="true"] QScrollBar,
QFrame#V3ExportPage QScrollBar {
    background: #080908;
    border: none;
}

#V2LibraryScreen[shellV3Library="true"] QScrollBar::handle:vertical,
#V2LibraryScreen[shellV3Library="true"] QScrollBar::handle:horizontal,
QFrame#V3ExportPage QScrollBar::handle:vertical,
QFrame#V3ExportPage QScrollBar::handle:horizontal {
    background: #232420;
    border: 1px solid #0a0b0a;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QScrollBar::handle:hover,
QFrame#V3ExportPage QScrollBar::handle:hover {
    background: #2c2d29;
}

#V2LibraryScreen[shellV3Library="true"] QScrollBar::add-line,
#V2LibraryScreen[shellV3Library="true"] QScrollBar::sub-line,
#V2LibraryScreen[shellV3Library="true"] QScrollBar::add-page,
#V2LibraryScreen[shellV3Library="true"] QScrollBar::sub-page,
QFrame#V3ExportPage QScrollBar::add-line,
QFrame#V3ExportPage QScrollBar::sub-line,
QFrame#V3ExportPage QScrollBar::add-page,
QFrame#V3ExportPage QScrollBar::sub-page {
    background: transparent;
    border: none;
}

/* Settings uses one chassis and flat page content; the controls carry bevel. */
QDialog#V3SettingsDialog {
    background: #0f100f;
}

QDialog#V3SettingsDialog QFrame#V3SettingsBody {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #141513,
        stop:0.10 #111210,
        stop:1 #0b0c0b);
    border: none;
    border-top: 1px solid #20211f;
    border-bottom: 1px solid #050505;
    border-radius: 0px;
}

QDialog#V3SettingsDialog QListWidget#V3SettingsNav {
    color: #aeb8b0;
    background: #10100f;
    border: none;
    border-right: 1px solid #050505;
}

QDialog#V3SettingsDialog QListWidget#V3SettingsNav::item:selected {
    color: #eef3ef;
    background: #152019;
    border-left: 3px solid #39e07a;
}

QDialog#V3SettingsDialog QStackedWidget#V3SettingsStack,
QDialog#V3SettingsDialog QStackedWidget#V3SettingsStack QScrollArea,
QDialog#V3SettingsDialog QStackedWidget#V3SettingsStack QScrollArea > QWidget,
QDialog#V3SettingsDialog QWidget[settingsPage="true"],
QDialog#V3SettingsDialog QWidget[settingsPage="true"] QFrame {
    background: #0f100f;
    border: none;
    border-radius: 0px;
}

QDialog#V3SettingsDialog QPushButton,
QDialog#V3SettingsDialog QLineEdit,
QDialog#V3SettingsDialog QComboBox,
QDialog#V3SettingsDialog QSpinBox,
QDialog#V3SettingsDialog QDoubleSpinBox {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a1b19,
        stop:0.10 #171816,
        stop:0.78 #121312,
        stop:1 #0c0d0c);
    border: 1px solid #060706;
    border-top: 1px solid #292a27;
    border-bottom: 1px solid #030403;
    border-radius: 3px;
}

QDialog#V3SettingsDialog QPushButton:focus,
QDialog#V3SettingsDialog QLineEdit:focus,
QDialog#V3SettingsDialog QComboBox:focus,
QDialog#V3SettingsDialog QSpinBox:focus,
QDialog#V3SettingsDialog QDoubleSpinBox:focus {
    border-left-color: #39e07a;
    border-top-color: #343531;
}

QDialog#V3SettingsDialog QPushButton:hover,
QDialog#V3SettingsDialog QLineEdit:focus,
QDialog#V3SettingsDialog QComboBox:focus,
QDialog#V3SettingsDialog QSpinBox:focus,
QDialog#V3SettingsDialog QDoubleSpinBox:focus {
    background: #1d1e1b;
}

QDialog#V3SettingsDialog QPushButton:disabled,
QDialog#V3SettingsDialog QLineEdit:disabled,
QDialog#V3SettingsDialog QComboBox:disabled,
QDialog#V3SettingsDialog QSpinBox:disabled,
QDialog#V3SettingsDialog QDoubleSpinBox:disabled {
    color: #626662;
    background: #111210;
    border-color: #060706;
    border-top-color: #20211f;
}

QDialog#V3SettingsDialog QPushButton#V3SettingsSave {
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-bottom-color: #1a843f;
}

/* New Project keeps the real form and one optical drop target. */
#V2NewProjectDialog[shellV3Dialog="true"] {
    background: #0f100f;
}

#V2NewProjectDialog[shellV3Dialog="true"] QFrame[card="true"] {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2NewProjectDialog[shellV3Dialog="true"] QFrame[dropzone="true"] {
    background: #0a0b0a;
    border: 1px dashed #343432;
    border-radius: 3px;
}

#V2NewProjectDialog[shellV3Dialog="true"] QPushButton,
#V2NewProjectDialog[shellV3Dialog="true"] QLineEdit {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a1b19,
        stop:0.10 #171816,
        stop:0.78 #121312,
        stop:1 #0c0d0c);
    border: 1px solid #060706;
    border-top: 1px solid #292a27;
    border-bottom: 1px solid #030403;
    border-radius: 3px;
}

#V2NewProjectDialog[shellV3Dialog="true"] QPushButton:focus,
#V2NewProjectDialog[shellV3Dialog="true"] QLineEdit:focus {
    border-left-color: #39e07a;
    border-top-color: #343531;
}

#V2NewProjectDialog[shellV3Dialog="true"] QPushButton:hover,
#V2NewProjectDialog[shellV3Dialog="true"] QLineEdit:focus {
    background: #1d1e1b;
}

#V2NewProjectDialog[shellV3Dialog="true"] QPushButton#NewProjectCreate {
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-bottom-color: #1a843f;
}

/* Recovery has one intentional summary instrument, not nested fact cards. */
QDialog#V3RecoveryDialog {
    background: #0f100f;
}

QDialog#V3RecoveryDialog QFrame#V3RecoverySummary {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #141513,
        stop:1 #0b0c0b);
    border: 1px solid #050505;
    border-top-color: #20211f;
    border-radius: 3px;
}

QDialog#V3RecoveryDialog QPushButton {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a1b19,
        stop:0.10 #171816,
        stop:0.78 #121312,
        stop:1 #0c0d0c);
    border: 1px solid #060706;
    border-top: 1px solid #292a27;
    border-bottom: 1px solid #030403;
    border-radius: 3px;
}

QDialog#V3RecoveryDialog QPushButton#V3RecoveryRestore {
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-bottom-color: #1a843f;
}

QDialog#V3RecoveryDialog QPushButton#V3RecoveryDiscard {
    color: #ff6565;
    background: #121212;
    border-color: #8d3131;
    border-top-color: #b64a4a;
}

QDialog#V3RecoveryDialog QPushButton:hover {
    background: #1d1e1b;
}

/* Detect keeps all three real stages on one near-black instrument chassis. */
#V2DetectPlaysDialog {
    background: #0f100f;
    border: 1px solid #050505;
    border-top-color: #20211f;
    border-radius: 3px;
}

#V2DetectPlaysDialog QFrame#DetectHeaderRule,
#V2DetectPlaysDialog QFrame#DetectStepRule,
#V2DetectPlaysDialog QFrame#DetectFooterRule,
#V2DetectPlaysDialog QFrame#DetectSettingsActionRule,
#V2DetectPlaysDialog QFrame[detectStepDivider="true"] {
    background: transparent;
    border: none;
}

#V2DetectPlaysDialog QWidget#DetectSetupPage,
#V2DetectPlaysDialog QWidget#DetectAnalyzePage,
#V2DetectPlaysDialog QWidget#DetectReviewPage,
#V2DetectPlaysDialog QFrame#DetectSetupGuidance,
#V2DetectPlaysDialog QFrame[detectSettings="true"] {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2DetectPlaysDialog QLabel[detectStep="true"] {
    color: #777d78;
    background: transparent;
    border: none;
}

#V2DetectPlaysDialog QLabel[detectStep="true"][active="true"] {
    color: #39e07a;
}

#V2DetectPlaysDialog QLabel#All22DetectionNotice {
    color: #e3be56;
    background: transparent;
    border: none;
    border-left: 2px solid #e3be56;
    border-radius: 0px;
}

#V2DetectPlaysDialog QFrame[detectAnalysisCard="true"] {
    background: #0a0b0a;
    border: none;
    border-top: 1px solid #1d1e1c;
    border-bottom: 1px solid #050505;
    border-radius: 3px;
}

#V2DetectPlaysDialog QPushButton,
#V2DetectPlaysDialog QDoubleSpinBox {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a1b19,
        stop:0.10 #171816,
        stop:0.78 #121312,
        stop:1 #0c0d0c);
    border: 1px solid #060706;
    border-top: 1px solid #292a27;
    border-bottom: 1px solid #030403;
    border-radius: 3px;
}

#V2DetectPlaysDialog QPushButton:focus,
#V2DetectPlaysDialog QDoubleSpinBox:focus {
    border-left-color: #39e07a;
    border-top-color: #343531;
}

#V2DetectPlaysDialog QPushButton:hover,
#V2DetectPlaysDialog QDoubleSpinBox:focus {
    background: #1d1e1b;
}

#V2DetectPlaysDialog QPushButton:disabled,
#V2DetectPlaysDialog QDoubleSpinBox:disabled {
    color: #626662;
    background: #111210;
    border-color: #060706;
    border-top-color: #20211f;
}

#V2DetectPlaysDialog QPushButton#DetectRunButton,
#V2DetectPlaysDialog QPushButton#DetectCreateClips {
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-bottom-color: #1a843f;
}

#V2DetectPlaysDialog QPushButton#DetectAllFilter:checked {
    color: #061108;
    background: #39e07a;
    border-color: #6ceca2;
}

#V2DetectPlaysDialog QPushButton#DetectReviewFilter:checked,
#V2DetectPlaysDialog QPushButton[selectedDecision="true"] {
    color: #111110;
    background: #e3be56;
    border-color: #e3be56;
}

#V2DetectPlaysDialog QTableWidget#DetectResultsTable,
#V2DetectPlaysDialog QTableWidget#DetectConfidentResultsTable {
    background: #070807;
    alternate-background-color: #0d0e0d;
    border: 1px solid #050505;
    border-top-color: #242422;
    border-radius: 2px;
    gridline-color: #171716;
}

#V2DetectPlaysDialog QTableWidget#DetectResultsTable QHeaderView::section,
#V2DetectPlaysDialog QTableWidget#DetectConfidentResultsTable QHeaderView::section {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #151614,
        stop:1 #0b0c0b);
    border: none;
    border-right: 1px solid #171716;
    border-bottom: 1px solid #050505;
}

#V2DetectPlaysDialog QFrame#DetectReviewDetail {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #141513,
        stop:1 #0b0c0b);
    border: none;
    border-top: 1px solid #20211f;
    border-bottom: 1px solid #050505;
    border-radius: 3px;
}

#V2DetectPlaysDialog QLabel[previewFrame="true"] {
    background: #070807;
    border: 1px solid #050505;
    border-top-color: #242422;
    border-radius: 2px;
}

/* Embedded Export and Package use dark instruments and ledger seams, not
   broad mid-gray cards. The 428px Review embedding remains untouched. */
QFrame#V3ExportPage,
QFrame#V3ExportPage QWidget {
    background: #090a09;
}

QFrame#V3ExportPage QLabel#V3ExportStageCell {
    background: transparent;
    border: none;
    border-bottom: 1px solid #171716;
    border-radius: 0px;
}

QFrame#V3ExportPage QLabel#V3ExportStageCell[active="true"] {
    color: #8df0b0;
    background: transparent;
    border: none;
    border-bottom: 2px solid #39e07a;
}

QFrame#V3ExportPage QFrame#V3ExportCard,
QFrame#V3ExportPage QFrame#V3ExportQueueCard {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #141513,
        stop:0.10 #111210,
        stop:1 #0b0c0b);
    border: none;
    border-top: 1px solid #20211f;
    border-bottom: 1px solid #050505;
    border-radius: 3px;
}

QFrame#V3ExportPage QFrame#V3ExportQueueLane,
QFrame#V3ExportPage QFrame#V3PackagePlayRow {
    background: #0d0e0d;
    border: none;
    border-bottom: 1px solid #050505;
    border-radius: 2px;
}

QFrame#V3ExportPage QCheckBox {
    background: transparent;
    border: none;
}

QFrame#V3ExportPage QComboBox,
QFrame#V3ExportPage QLabel#V3ExportDestination,
QFrame#V3ExportPage QPushButton#V3ExportBackToReview,
QFrame#V3ExportPage QPushButton#V3ExportCancel,
QFrame#V3ExportPage QPushButton#V3ExportOpenOutput,
QFrame#V3ExportPage QPushButton#V3ExportShowFolder {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a1b19,
        stop:0.10 #171816,
        stop:0.78 #121312,
        stop:1 #0c0d0c);
    border: 1px solid #060706;
    border-top: 1px solid #292a27;
    border-bottom: 1px solid #030403;
    border-radius: 3px;
}

QFrame#V3ExportPage QComboBox:focus,
QFrame#V3ExportPage QPushButton:focus {
    border-left-color: #39e07a;
    border-top-color: #343531;
}

QFrame#V3ExportPage QComboBox:hover,
QFrame#V3ExportPage QComboBox:focus,
QFrame#V3ExportPage QPushButton:hover,
QFrame#V3ExportPage QPushButton:focus {
    background: #1d1e1b;
}

QDialog#V3SettingsDialog QScrollBar,
#V2DetectPlaysDialog QScrollBar {
    background: #080908;
    border: none;
}

QDialog#V3SettingsDialog QScrollBar::handle:vertical,
QDialog#V3SettingsDialog QScrollBar::handle:horizontal,
#V2DetectPlaysDialog QScrollBar::handle:vertical,
#V2DetectPlaysDialog QScrollBar::handle:horizontal {
    background: #232420;
    border: 1px solid #0a0b0a;
    border-radius: 3px;
}

QDialog#V3SettingsDialog QScrollBar::handle:hover,
#V2DetectPlaysDialog QScrollBar::handle:hover {
    background: #2c2d29;
}

QDialog#V3SettingsDialog QScrollBar::add-line,
QDialog#V3SettingsDialog QScrollBar::sub-line,
#V2DetectPlaysDialog QScrollBar::add-line,
#V2DetectPlaysDialog QScrollBar::sub-line,
QDialog#V3SettingsDialog QScrollBar::add-page,
QDialog#V3SettingsDialog QScrollBar::sub-page,
#V2DetectPlaysDialog QScrollBar::add-page,
#V2DetectPlaysDialog QScrollBar::sub-page {
    background: transparent;
    border: none;
}

#V2LibraryScreen[shellV3Library="true"]
QPushButton#V3LibraryExport:enabled {
    color: #07130b;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #54ea8d,
        stop:0.12 #39e07a,
        stop:1 #24bf62);
    border-top-color: #74f0a4;
    border-bottom-color: #13743a;
}

#V2LibraryScreen[shellV3Library="true"]
QPushButton#V3LibraryExport:enabled:hover {
    color: #041007;
    background: #55e990;
    border-top-color: #8af3b2;
}

QFrame#V3ExportPage QPushButton#V3ExportStart,
QFrame#V3ExportPage QPushButton#V3PackageStart {
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-bottom-color: #1a843f;
}

QFrame#V3ExportPage QPushButton#V3ExportStart:disabled,
QFrame#V3ExportPage QPushButton#V3PackageStart:disabled {
    color: #59625b;
    background: #111110;
    border-color: #242422;
}

QFrame#V3ExportPage QProgressBar#V3ExportProgress {
    background: #070807;
    border: 1px solid #171716;
    border-radius: 2px;
}

QFrame#V3ExportPage QProgressBar#V3ExportProgress::chunk {
    background: #39e07a;
    border-radius: 1px;
}
"""


V3_OPTION1_HOME_MATERIAL = r"""
/* Selected Home Option 1: one flat canvas. Hierarchy comes from spacing and
   type; only interactive controls keep a visible boundary. */
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] {
    background: #080a0b;
    border: none;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="home"]
QPushButton#V3HomeNavigationChip {
    color: #39e07a;
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] {
    background: #070a0c;
}

#V2StartScreen[shellV3Home="true"] QLabel#HomeWelcomeTitle {
    font-size: 28px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="roomSubtitle"] {
    font-size: 23px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="eyebrow"],
#V2StartScreen[shellV3Home="true"] QLabel[role="projectMeta"] {
    font-size: 12px;
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunLeft,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunDropHost,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningStart,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeStatement,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeBody,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects,
#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunActions,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeWorkflow,
#V2StartScreen[shellV3Home="true"] QScrollArea,
#V2StartScreen[shellV3Home="true"] QScrollArea > QWidget,
#V2StartScreen[shellV3Home="true"] QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"],
#V2StartScreen[shellV3Home="true"] QPushButton#V3HomeAddProject {
    background: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"]:hover,
#V2StartScreen[shellV3Home="true"] QPushButton#V3HomeAddProject:hover {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"]:focus,
#V2StartScreen[shellV3Home="true"] QPushButton#V3HomeAddProject:focus {
    background: transparent;
    border: none;
    border-left: 2px solid #39e07a;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton#V3HomeFirstRunNewProject[dropTarget="true"] {
    background: transparent;
    border: none;
}

#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"] {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"] > .QWidget {
    background: transparent;
    border: none;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="projectTitle"] {
    font-size: 20px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="projectSource"] {
    font-size: 13px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="metricValue"],
#V2StartScreen[shellV3Home="true"] QLabel[role="metricAccent"] {
    font-size: 23px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="metricLabel"] {
    font-size: 9px;
}

#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction {
    min-width: 130px;
    max-width: 130px;
}

#V2StartScreen[shellV3Home="true"] QToolButton#ProjectOverflowMenu {
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    padding: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"]:hover {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QLabel[projectThumb="true"] {
    background: #050708;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame[addProjectRow="true"],
#V2StartScreen[shellV3Home="true"] QPushButton#V3HomeAddProject {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QPushButton#V3HomeAddProject {
    min-height: 96px;
    max-height: 96px;
}

#V2StartScreen[shellV3Home="true"] QToolButton[projectMenu="true"] {
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QToolButton[projectMenu="true"]:hover,
#V2StartScreen[shellV3Home="true"] QToolButton[projectMenu="true"]:focus {
    color: #39e07a;
    background: transparent;
    border: none;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone {
    background: #080b0c;
    border: 1px dashed #28362f;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone[dragover="true"] {
    background: #0a1411;
    border: 1px solid #39e07a;
    border-radius: 0px;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeAcceptedFile {
    background: #0a100d;
    border: 1px solid #286642;
    border-radius: 0px;
}
"""


V3_FLAT_MATERIAL = r"""
/* Approved flat surfaces; the existing V3 widgets own layout and behavior. */
QWidget#TapeSiftV3ApplicationBar,
QWidget#TapeSiftV3ApplicationBar[pageMode="home"],
QWidget#TapeSiftV3ApplicationBar[pageMode="review"],
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] {
    background: #080c0d;
    border: none;
    border-bottom: 1px solid #17211d;
}

QFrame#V3ReviewWorkspace,
QStackedWidget#V3ReviewCenterStack,
QWidget#V2ReviewPlayerPanel {
    background: #080c0d;
    border: none;
}

QFrame#V3LedgerRail,
QFrame#V3DetailsRail,
QFrame#V3OpenRail,
QFrame#V3CollapsedRail,
QFrame#V3RailCollapseStrip,
QFrame#V3ReviewWorkspace #V2ReviewClipList,
QFrame#V3ReviewWorkspace #V2ReviewInspector {
    background: #0b1010;
    border: none;
    border-radius: 0px;
}

QFrame#V3LedgerRail { border-right: 1px solid #1e2b25; }
QFrame#V3DetailsRail { border-left: 1px solid #1e2b25; }

QToolButton#V3RailExpandButton,
QToolButton#V3RailCollapseButton {
    color: #9eaaa3;
    background: transparent;
    border: 1px solid #26352d;
    border-radius: 2px;
}
QToolButton#V3RailExpandButton:hover,
QToolButton#V3RailCollapseButton:hover {
    color: #39e07a;
    background: #0d1712;
    border-color: #39e07a;
}
QLabel#V3CollapsedRailSelection {
    color: #e3e9e5;
    background: transparent;
    border: none;
    border-top: 1px solid #245336;
    border-bottom: 1px solid #245336;
    border-radius: 0px;
}

QFrame#V3ReviewWorkspace #ClipLedgerHeader,
QFrame#V3ReviewWorkspace #InspectorHeader {
    background: transparent;
    border: none;
    border-bottom: 1px solid #233029;
}

QFrame#V3ReviewStatusBadge {
    color: #70e99f;
    background: transparent;
    border: 1px solid #24633c;
    border-radius: 2px;
}

QFrame#V3LedgerProjectCard,
QFrame#V3LedgerCommandCard,
QFrame#V3ReviewWorkspace #InspectorSummaryCard,
QFrame#V3RangeCard,
QFrame#V3ReviewWorkspace #InspectorPrimaryFields,
QFrame#V3ReviewWorkspace QGroupBox#InspectorPlayers,
QFrame#V3ReviewWorkspace QGroupBox#InspectorNotes,
QFrame#V3ReviewWorkspace QGroupBox#ClipEditorDetails,
QFrame#V3ReviewWorkspace #InspectorActionBar,
QFrame#V3ReviewActionMirror {
    background: transparent;
    border-top: 0px;
    border-left: 0px;
    border-right: 0px;
    border-bottom: 1px solid #1f2b25;
    border-radius: 0px;
}

QFrame#V3LedgerProjectCard,
QFrame#V3LedgerCommandCard {
    margin-left: 10px;
    margin-right: 10px;
}

QFrame#V3ReviewWorkspace #InspectorSummaryCard {
    margin: 8px 10px 2px 10px;
}

QFrame#V3RangeCard,
QFrame#V3ReviewWorkspace QGroupBox#InspectorPlayers,
QFrame#V3ReviewWorkspace QGroupBox#InspectorNotes {
    margin: 2px 10px;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QLineEdit,
QFrame#V3ReviewWorkspace #V2ReviewInspector QLineEdit,
QFrame#V3ReviewWorkspace #V2ReviewInspector QComboBox,
QFrame#V3ReviewWorkspace #V2ReviewInspector QPlainTextEdit,
QFrame#V3RangeCard QLabel#InspectorRangeDuration {
    color: #dbe3de;
    background: #080d0c;
    border: 1px solid #293a31;
    border-radius: 2px;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QPushButton,
QFrame#V3ReviewWorkspace #V2ReviewClipList QToolButton,
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton,
QFrame#V3ReviewActionMirror QPushButton {
    color: #d0d9d3;
    background: transparent;
    border: 1px solid #2b3b32;
    border-radius: 2px;
}
QFrame#V3ReviewWorkspace #V2ReviewClipList QPushButton:hover,
QFrame#V3ReviewWorkspace #V2ReviewClipList QToolButton:hover,
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton:hover,
QFrame#V3ReviewActionMirror QPushButton:hover {
    color: #f0f5f2;
    background: #0d1712;
    border-color: #3e7451;
}
QFrame#V3ReviewWorkspace #V2ReviewClipList QPushButton:checked,
QFrame#V3ReviewWorkspace #V2ReviewClipList QToolButton[accent="true"],
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton#InspectorSaveNext,
QFrame#V3ReviewActionMirror QPushButton[primary="true"] {
    color: #041008;
    background: #39e07a;
    border: 1px solid #39e07a;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget {
    color: #d2dad5;
    background: transparent;
    alternate-background-color: #0b1110;
    border: none;
    gridline-color: #17221d;
    selection-background-color: #0d2b1a;
}
QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget::item {
    border: none;
    border-bottom: 1px solid #17221d;
}
QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget::item:selected {
    background: #10331f;
    border-left: 2px solid #39e07a;
}

QFrame#V3ReviewWorkspace QToolButton#InspectorMoreDetailsToggle,
QFrame#V3ReviewWorkspace QToolButton#InspectorAdvancedDetailsToggle {
    color: #d8e1db;
    background: transparent;
    border: none;
    border-top: 1px solid #1f2b25;
    border-bottom: 1px solid #1f2b25;
    border-radius: 0px;
}

QWidget#InspectorNamingExportBody,
QWidget#InspectorDetailAddRow {
    background: transparent;
    border: none;
}

QFrame#V3ReviewWorkspace #InspectorActionBar {
    margin: 4px 10px 8px 10px;
    padding: 4px 0px 0px 0px;
    border-top: 1px solid #26362d;
    border-left: 0px;
    border-right: 0px;
    border-bottom: 0px;
}

QWidget#V3TimelineLabelBand,
QWidget#PlayerStripSlot,
#TagMapHeader,
#V2AttributeGrid,
QFrame#V3ReviewFooter,
QFrame#V3ReviewFooterLeft,
QFrame#V3ReviewFooterCenter,
QFrame#V3ReviewFooterRight {
    background: #090d0d;
    border-radius: 0px;
}

/* Home: open layout, never a card stack. */
#V2StartScreen[shellV3Home="true"],
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeBody,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunLeft,
#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunActions,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects {
    background: #080c0d;
    border: none;
    border-radius: 0px;
}
#V2StartScreen[shellV3Home="true"] QPushButton[homeFirstRunAction="true"],
#V2StartScreen[shellV3Home="true"] QPushButton[addProjectRow="true"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 0px;
}
#V2StartScreen[shellV3Home="true"] QPushButton[homeFirstRunAction="true"]:hover,
#V2StartScreen[shellV3Home="true"] QPushButton[addProjectRow="true"]:hover {
    background: #0c1511;
    border-left: 2px solid #39e07a;
}

QScrollArea#V3LibraryInspectorScroll,
QScrollArea#V3LibraryInspectorScroll > QWidget {
    background: #080c0d;
    border: none;
}

/* Library: one canvas, rows and controls rather than framed modules. */
#V2LibraryScreen[shellV3Library="true"],
#V2LibraryScreen[shellV3Library="true"] QWidget#V3LibraryMainSurface,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryPreview,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryLedger {
    background: #080c0d;
    border: none;
    border-radius: 0px;
}
#V2LibraryScreen[shellV3Library="true"] QLineEdit,
#V2LibraryScreen[shellV3Library="true"] QComboBox,
#V2LibraryScreen[shellV3Library="true"] QPlainTextEdit {
    background: #0a0f0e;
    border: 1px solid #293930;
    border-radius: 2px;
}

/* Export and package: stage lanes, not raised cards. */
QFrame#V3ExportPage,
QFrame#V3ExportPage QWidget,
QFrame#V3ExportCard,
QFrame#V3ExportQueueCard,
QFrame#V3ExportQueueLane,
QFrame#V3PackagePlayRow {
    background: #080c0d;
    border: none;
    border-radius: 0px;
}
QFrame#V3ExportCard,
QFrame#V3ExportQueueCard {
    border-top: 1px solid #233128;
}
QLabel#V3ExportStageCell {
    background: transparent;
    border: none;
    border-bottom: 1px solid #223029;
    border-radius: 0px;
}
QLabel#V3ExportStageCell[active="true"] {
    background: transparent;
    border: none;
    border-bottom: 2px solid #39e07a;
}

/* Dialogs: flat columns with restrained input outlines. */
QDialog[shellV3Dialog="true"],
QDialog[tapesiftDialog="true"],
QWidget#V3SettingsBody,
QListWidget#V3SettingsNav,
QStackedWidget#V3SettingsStack,
QScrollArea#V3SettingsPageScroll,
QFrame#V3RecoverySummary {
    background: #0b0f0e;
    border: none;
    border-radius: 0px;
}
QListWidget#V3SettingsNav::item {
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
}
QListWidget#V3SettingsNav::item:selected {
    background: #0d1712;
    border-left: 2px solid #39e07a;
}

/* Final reset for legacy panel borders that otherwise survive shorthand QSS. */
#V3LedgerProjectCard,
#V3LedgerCommandCard,
#InspectorSummaryCard,
#V3RangeCard,
#InspectorPrimaryFields,
#InspectorPlayers,
#InspectorNotes,
#ClipEditorDetails,
#InspectorActionBar,
#V3ExportCard,
#V3ExportQueueCard,
#V3ExportQueueLane,
#V3PackagePlayRow {
    background: transparent;
    border: 0px;
    border-radius: 0px;
}
#V3LedgerProjectCard,
#InspectorSummaryCard,
#V3RangeCard,
#InspectorPlayers,
#InspectorNotes {
    border-bottom: 1px solid #1f2b25;
}
#InspectorActionBar,
#V3ExportCard,
#V3ExportQueueCard {
    border-top: 1px solid #26362d;
}

QDialog[tapesiftDialog="true"] QLineEdit,
QDialog[tapesiftDialog="true"] QComboBox,
QDialog[tapesiftDialog="true"] QSpinBox,
QDialog[tapesiftDialog="true"] QDoubleSpinBox,
QDialog[tapesiftDialog="true"] QPlainTextEdit,
QDialog[shellV3Dialog="true"] QLineEdit,
QDialog[shellV3Dialog="true"] QComboBox,
QDialog[shellV3Dialog="true"] QSpinBox,
QDialog[shellV3Dialog="true"] QDoubleSpinBox,
QDialog[shellV3Dialog="true"] QPlainTextEdit {
    color: #dce4df;
    background: #080d0c;
    border: 1px solid #293930;
    border-radius: 2px;
}
QDialog[tapesiftDialog="true"] QPushButton,
QDialog[shellV3Dialog="true"] QPushButton {
    color: #d5ddd8;
    background: transparent;
    border: 1px solid #2d3d34;
    border-radius: 2px;
}
QDialog[tapesiftDialog="true"] QPushButton[primary="true"],
QDialog[tapesiftDialog="true"] QPushButton[accent="true"],
QDialog[shellV3Dialog="true"] QPushButton[primary="true"] {
    color: #041008;
    background: #39e07a;
    border-color: #39e07a;
}
#DetectSetupSettings,
#DetectAnalysisCard,
#DetectReviewDetail,
#DetectSettingsBar {
    background: transparent;
    border: 0px;
    border-radius: 0px;
}

/* Match the specificity of the inherited material rules. */
QMainWindow#TapeSiftV3,
QFrame#V3ReviewRow,
QFrame#V3ReviewWorkspace #V2ReviewClipList,
QFrame#V3ReviewWorkspace #V2ReviewInspector,
QFrame#V3ReviewWorkspace #V2ReviewInspector QScrollArea,
QFrame#V3ReviewWorkspace #V2ReviewInspector QScrollArea > QWidget,
QFrame#V3ReviewWorkspace #V2ReviewInspector QScrollArea > QWidget > QWidget {
    background: #080c0d;
    border: none;
    border-radius: 0px;
}
QFrame#V3ReviewRow {
    border-bottom: 1px solid #17211d;
}

QFrame#V3ReviewWorkspace #ClipLedgerHeader,
QFrame#V3ReviewWorkspace #InspectorHeader {
    background: transparent;
    border: none;
    border-bottom: 1px solid #1c2923;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorSummaryCard,
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorPrimaryFields,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#InspectorPlayers,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#InspectorNotes,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#ClipEditorDetails,
QFrame#V3ReviewWorkspace #V3RangeCard {
    background: transparent;
    border: none;
    border-radius: 0px;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorSummaryCard QWidget,
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorHeader QLabel,
QFrame#V3ReviewWorkspace #V2ReviewClipList #ClipLedgerHeader QLabel {
    background: transparent;
    border: none;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector QWidget#V3DetailsHeading,
QFrame#V3ReviewWorkspace #V2ReviewInspector QWidget#V3DetailsHeading QLabel,
QFrame#V3ReviewWorkspace #V2ReviewInspector QWidget#InspectorAdvancedDetails,
QFrame#V3ReviewWorkspace #V2ReviewInspector QWidget#InspectorAdvancedDetailsBody,
QFrame#V3ReviewWorkspace #V2ReviewInspector QWidget#InspectorMoreDetails,
QFrame#V3ReviewWorkspace #V2ReviewInspector QWidget#InspectorMoreDetailsBody {
    background: transparent;
    border: none;
    border-radius: 0px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorSummaryCard,
QFrame#V3ReviewWorkspace #V3RangeCard,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#InspectorPlayers,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#InspectorNotes {
    border-bottom: 1px solid #1c2923;
}

#V2ReviewInspector QWidget#InspectorPlayRail,
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="run"],
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="pass"],
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="rpo"],
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="screen"],
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="sack"],
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="interception"],
#V2ReviewInspector QWidget#InspectorPlayRail[playKind="touchdown"] {
    background: #39e07a;
    border: none;
    border-radius: 0px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector
QToolButton#InspectorMoreDetailsToggle,
QFrame#V3ReviewWorkspace #V2ReviewInspector
QToolButton#InspectorAdvancedDetailsToggle {
    color: #dce4df;
    background: transparent;
    border: none;
    border-top: 1px solid #1c2923;
    border-radius: 0px;
    font-family: "Segoe UI";
    font-size: 11px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorNamingExportBody,
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorDetailAddRow,
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorNamingExportBody QWidget {
    background: transparent;
    border: none;
    border-radius: 0px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorNamingExportBody QPushButton {
    color: #dce4df;
    background: transparent;
    border: 1px solid #2a3b32;
    border-radius: 2px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector {
    font-family: "Segoe UI";
    font-size: 11px;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector QLabel[role="heading"],
QFrame#V3ReviewWorkspace #V2ReviewClipList QLabel[role="heading"] {
    font-family: "Segoe UI";
    font-size: 15px;
    font-weight: 600;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector QLabel#InspectorTitleSummary {
    font-family: "Segoe UI";
    font-size: 15px;
    font-weight: 600;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox::title,
QLabel#V3RangeTitle,
QFrame#V3PlayActionsHeader QLabel {
    font-family: "Segoe UI";
    font-size: 11px;
    font-weight: 600;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector QLineEdit,
QFrame#V3ReviewWorkspace #V2ReviewInspector QComboBox,
QFrame#V3ReviewWorkspace #V2ReviewInspector QPlainTextEdit {
    font-family: "Segoe UI";
    font-size: 11px;
    background: #070d0b;
    border: 1px solid #2a3b32;
    border-radius: 2px;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector QComboBox {
    font-size: 10px;
    padding-left: 4px;
    padding-right: 16px;
}
QFrame#V3ReviewWorkspace QFrame#V3RangeCard QLineEdit#V3RangeStart,
QFrame#V3ReviewWorkspace QFrame#V3RangeCard QLineEdit#V3RangeEnd,
QFrame#V3ReviewWorkspace QFrame#V3RangeCard QLabel#InspectorRangeDuration {
    color: #dce4df;
    background: #070d0b;
    border: 1px solid #2a3b32;
    border-radius: 2px;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector
QWidget#InspectorAdvancedDetailsBody QLabel[role="subtle"] {
    font-family: "Segoe UI";
    font-size: 11px;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorActionBar {
    background: #080c0d;
    border: none;
    border-top: 1px solid #26372e;
    border-radius: 0px;
}
#V2ReviewInspector #InspectorActionBar QPushButton,
QFrame#V3ReviewActionMirror QPushButton {
    color: #dce4df;
    background: transparent;
    border: 1px solid #2a3b32;
    border-radius: 2px;
}
#V2ReviewInspector #InspectorActionBar QPushButton#InspectorSaveNext,
QFrame#V3ReviewActionMirror QPushButton[primary="true"] {
    color: #041008;
    background: #39e07a;
    border: 1px solid #39e07a;
}

/* Home: an idle drop target is content, not a giant card. */
#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone {
    background: transparent;
    border: none;
    border-radius: 0px;
}
#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone[dragover="true"] {
    background: #09130f;
    border: 1px solid #39e07a;
    border-radius: 0px;
}

/* Library: one working canvas.  Rows and form controls provide structure. */
#V2LibraryScreen[shellV3Library="true"],
#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryWorkbench,
#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryFilmPanel,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector,
#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryLedgerHeader,
#V2LibraryScreen[shellV3Library="true"] QListWidget#V2LibraryResults,
#V2LibraryScreen[shellV3Library="true"] QFrame#V3LibraryLedgerEmpty {
    background: #080c0d;
    border: none;
    border-radius: 0px;
}
#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch,
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QLineEdit,
#V2LibraryScreen[shellV3Library="true"] QComboBox,
#V2LibraryScreen[shellV3Library="true"] QPlainTextEdit {
    color: #dce4df;
    background: #070d0b;
    border: 1px solid #293a31;
    border-radius: 2px;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton,
#V2LibraryScreen[shellV3Library="true"] QToolButton,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QToolButton {
    color: #dce4df;
    background: transparent;
    border: 1px solid #26372e;
    border-radius: 2px;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryExport:enabled {
    color: #041008;
    background: #39e07a;
    border: 1px solid #39e07a;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryEmptyRebuild,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton#V3LibraryRebuild,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton#V3LibraryBack,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QToolButton#V3LibraryHome {
    color: #dce4df;
    background: transparent;
    border: 1px solid #293a31;
    border-radius: 2px;
}
#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibraryClipTitle {
    color: #eef3ef;
    background: transparent;
    border: none;
    border-bottom: 1px solid #1c2923;
    border-radius: 0px;
}
#V2LibraryScreen[shellV3Library="true"]
QWidget#V2LibraryInspector QLineEdit,
#V2LibraryScreen[shellV3Library="true"]
QWidget#V2LibraryInspector QPlainTextEdit,
#V2LibraryScreen[shellV3Library="true"]
QWidget#V2LibraryInspector QComboBox {
    color: #dce4df;
    background: #070d0b;
    border: 1px solid #293a31;
    border-radius: 2px;
}
#V2LibraryScreen[shellV3Library="true"]
QWidget#V2LibraryInspector QLineEdit#V2LibraryClipTitle {
    background: transparent;
    border: none;
    border-bottom: 1px solid #1c2923;
    border-radius: 0px;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton[projectAction="true"],
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBuildReel:disabled,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryExport:disabled {
    color: #dce4df;
    background: transparent;
    border: 1px solid #293a31;
    border-radius: 2px;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBuildReel:disabled,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryExport:disabled {
    color: #59645d;
}

/* Dialogs and detection: plain sheets with only functional control outlines. */
QDialog#V3SettingsDialog,
QDialog#V3RecoveryDialog,
QDialog#V2NewProjectDialog,
QDialog#V2DetectPlaysDialog,
QDialog#V3SettingsDialog QFrame#V3SettingsBody,
QDialog#V3SettingsDialog QListWidget#V3SettingsNav,
QDialog#V3SettingsDialog QStackedWidget#V3SettingsStack,
QDialog#V3RecoveryDialog QFrame#V3RecoverySummary,
QDialog#V3SettingsDialog QStackedWidget#V3SettingsStack > QWidget,
QDialog#V3SettingsDialog QScrollArea#V3SettingsPageScroll,
QDialog#V3SettingsDialog QScrollArea#V3SettingsPageScroll > QWidget,
QDialog#V3SettingsDialog QScrollArea#V3SettingsPageScroll > QWidget > QWidget,
QDialog#V2DetectPlaysDialog QWidget#DetectSetupPage,
QDialog#V2DetectPlaysDialog QWidget#DetectAnalyzePage,
QDialog#V2DetectPlaysDialog QWidget#DetectReviewPage,
QDialog#V2DetectPlaysDialog QFrame#DetectSetupSettings,
QDialog#V2DetectPlaysDialog QFrame#DetectSetupGuidance,
QDialog#V2DetectPlaysDialog QFrame#DetectAnalysisCard,
QDialog#V2DetectPlaysDialog QFrame#DetectReviewDetail {
    background: #080c0d;
    border: none;
    border-radius: 0px;
}
QDialog#V2NewProjectDialog QFrame#NewProjectFilmDropZone {
    background: transparent;
    border: none;
    border-radius: 0px;
}
QDialog#V2NewProjectDialog QFrame#NewProjectColumnDivider,
QDialog#V2NewProjectDialog QFrame#NewProjectHeaderRule,
QDialog#V2DetectPlaysDialog QFrame#DetectStepRule,
QDialog#V2DetectPlaysDialog QFrame#DetectSettingsActionRule {
    background: #1d2a24;
    border: none;
}
QDialog#V2DetectPlaysDialog QLabel#All22DetectionNotice {
    color: #d9b43b;
    background: transparent;
    border: none;
    border-left: 2px solid #816b24;
    border-radius: 0px;
    padding-left: 12px;
}
QDialog#V3SettingsDialog QLineEdit,
QDialog#V3SettingsDialog QComboBox,
QDialog#V3SettingsDialog QSpinBox,
QDialog#V3SettingsDialog QDoubleSpinBox,
QDialog#V2NewProjectDialog QLineEdit,
QDialog#V2DetectPlaysDialog QDoubleSpinBox {
    color: #dce4df;
    background: #070d0b;
    border: 1px solid #293a31;
    border-radius: 2px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLineEdit {
    color: #dce4df;
    background: #070d0b;
    border: 1px solid #293a31;
    border-radius: 2px;
}
QDialog#V3SettingsDialog QPushButton,
QDialog#V3RecoveryDialog QPushButton,
QDialog#V2NewProjectDialog QPushButton,
QDialog#V2DetectPlaysDialog QPushButton {
    color: #dce4df;
    background: transparent;
    border: 1px solid #293a31;
    border-radius: 2px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton {
    color: #dce4df;
    background: transparent;
    border: 1px solid #293a31;
    border-radius: 2px;
}
QDialog#V2DetectPlaysDialog QPushButton#DetectRunButton,
QDialog#V2DetectPlaysDialog QPushButton[primary="true"],
QDialog#V2NewProjectDialog QPushButton[primary="true"],
QDialog#V3SettingsDialog QPushButton#V3SettingsSave,
QDialog#V3RecoveryDialog QPushButton#V3RecoveryRestore {
    color: #041008;
    background: #39e07a;
    border: 1px solid #39e07a;
}
QDialog#V2DetectPlaysDialog QTableWidget#DetectResultsTable,
QDialog#V2DetectPlaysDialog QTableWidget#DetectConfidentResultsTable,
QDialog#V2DetectPlaysDialog QTableWidget#DetectResultsTable QHeaderView::section,
QDialog#V2DetectPlaysDialog QTableWidget#DetectConfidentResultsTable QHeaderView::section {
    background: #080c0d;
    border: none;
    border-bottom: 1px solid #1c2923;
    border-radius: 0px;
}
QDialog#V2DetectPlaysDialog QTableWidget#DetectResultsTable::item,
QDialog#V2DetectPlaysDialog QTableWidget#DetectConfidentResultsTable::item {
    background: transparent;
    border: none;
    border-bottom: 1px solid #17211d;
    padding: 2px 4px;
}
QDialog#V2DetectPlaysDialog QTableWidget#DetectResultsTable::item:selected {
    background: #302d1d;
    border: none;
    border-bottom: 1px solid #675c2d;
    padding: 2px 3px;
}
QDialog#V2DetectPlaysDialog QWidget#DetectPreviewStrip,
QDialog#V2DetectPlaysDialog QLabel[previewFrame="true"] {
    background: #070b0a;
    border: none;
    border-top: 1px solid #17211d;
    border-radius: 0px;
}

QDialog#V3SettingsDialog QCheckBox {
    background: transparent;
    border: none;
    spacing: 8px;
}
QDialog#V3SettingsDialog QLabel {
    background: transparent;
    border: none;
}
QDialog#V3SettingsDialog QLabel#FirstReadSecurityPromise,
QDialog#V3SettingsDialog QLabel#FirstReadAnalystPromise {
    background: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
}
QDialog#V3SettingsDialog QCheckBox::indicator {
    width: 14px;
    height: 14px;
    background: #070d0b;
    border: 1px solid #405247;
    border-radius: 2px;
}
QDialog#V3SettingsDialog QCheckBox::indicator:checked {
    background: #39e07a;
    border-color: #39e07a;
}

/* Export: workflow lanes separated by rhythm and hairlines, never cards. */
QFrame#V3ExportPage,
QFrame#V3ExportPage QWidget,
QFrame#V3ExportPage QFrame#V3ExportCard,
QFrame#V3ExportPage QFrame#V3ExportQueueCard,
QFrame#V3ExportPage QFrame#V3ExportQueueLane,
QFrame#V3ExportPage QFrame#V3PackagePlayRow {
    background: #080c0d;
    border: none;
    border-radius: 0px;
}
QFrame#V3ExportPage QFrame#V3ExportCard,
QFrame#V3ExportPage QFrame#V3ExportQueueCard {
    border-top: 1px solid #1c2923;
}
QFrame#V3ExportPage QLabel#V3ExportStageCell {
    background: transparent;
    border: none;
    border-bottom: 1px solid #1c2923;
    border-radius: 0px;
}
QFrame#V3ExportPage QPushButton#V3ExportBackToReview,
QFrame#V3ExportPage QPushButton#V3ExportCancel,
QFrame#V3ExportPage QPushButton#V3ExportOpenOutput,
QFrame#V3ExportPage QPushButton#V3ExportShowFolder {
    color: #dce4df;
    background: transparent;
    border: 1px solid #293a31;
    border-radius: 2px;
}
"""



V3_HOME_INTAKE_MATERIAL = r"""
QWidget#V2StartScreen[shellV3Home="true"] {
    background: #080a0b;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel[role="roomSubtitle"] {
    color: #c7cbc8; font-size: 19px;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel[role="queueHeading"] {
    color: #dce0db; font-family: "Segoe UI"; font-size: 18px;
    font-weight: 400; letter-spacing: 0px;
}
QWidget#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"] {
    border: none; border-bottom: 1px solid #1b1e1f; border-radius: 0px;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel[role="projectTitle"] {
    color: #f1f2ee; font-family: "Segoe UI"; font-size: 20px; font-weight: 500;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel[role="projectSource"],
QWidget#V2StartScreen[shellV3Home="true"] QLabel#V3HomeProjectCounts {
    color: #bec3c0; font-family: "Segoe UI"; font-size: 16px; font-weight: 400;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel[role="projectMeta"] {
    color: #bdc1be; font-family: "Segoe UI"; font-size: 13px; letter-spacing: 0px;
}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction {
    min-width: 154px; max-width: 154px; min-height: 38px; max-height: 38px;
    padding: 0px; border: 1px solid #29583f; border-radius: 3px;
    color: #72ca99; background: transparent; font-family: "Segoe UI";
    font-size: 13px; font-weight: 500;
}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction:hover,
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction:focus {
    color: #abedc7; border-color: #62c995; background: #102019;
}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction:pressed {
    background: #183526;
}
QWidget#V2StartScreen[shellV3Home="true"] QProgressBar[logging="true"] {
    background: #292d2a; border: none; border-radius: 2px;
}
QWidget#V2StartScreen[shellV3Home="true"] QProgressBar[logging="true"]::chunk {
    background: #62c995; border-radius: 2px;
}
QWidget#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFooterRule,
QWidget#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunDivider {
    background: #1b1e1f; border: none;
}
QWidget#V2StartScreen[shellV3Home="true"] QWidget#V3HomeStatement {
    border: none; border-bottom: 1px solid #1b1e1f;
}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[libraryEntry="true"] {
    background: transparent; border: none; color: #c7cbc5;
    min-width: 110px; max-width: 110px;
    font-family: "Segoe UI"; font-size: 14px; padding: 0px;
}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[libraryEntry="true"]:hover,
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[libraryEntry="true"]:focus {
    color: #83d6a6; border-bottom: 1px solid #62c995;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] {
    background: #0c1010; border: none; color: #d9dbd3;
    font-family: "Segoe UI";
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLabel {
    background: transparent; border: none; color: #b7b9af;
    font-family: "Segoe UI"; font-size: 14px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLabel[role="dialogTitle"] {
    color: #e5e4d9; font-size: 34px; font-weight: 700;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLabel[role="subtle"] {
    color: #a9ada3; font-size: 15px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLabel[role="newProjectStep"] {
    color: #68c68a; font-family: "Consolas"; font-size: 13px;
    font-weight: 400; letter-spacing: 1px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLabel[role="newProjectFieldLabel"] {
    color: #b3b5ab; font-family: "Consolas"; font-size: 12px;
    font-weight: 400; letter-spacing: 0.6px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLabel#NewProjectFilmHeading {
    color: #d9dbd3; font-size: 16px; font-weight: 500;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QFrame#NewProjectFilmDropZone {
    background: transparent; border: none; border-radius: 0px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QFrame#NewProjectFilmDropZone[dragover="true"] {
    background: #102018; border: 1px solid #62c995;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QFrame#NewProjectColumnDivider,
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QFrame#NewProjectFooterRule {
    background: #353d39; border: none;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLineEdit {
    color: #e2e3da; background: #0a0e0e; border: 1px solid #3a423d;
    border-radius: 3px; padding: 0px 12px; min-height: 48px; max-height: 48px;
    font-family: "Segoe UI"; font-size: 16px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLineEdit:focus {
    border-color: #70c992;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton {
    background: #0a0e0e; color: #d5d8cf; border: 1px solid #3a423d;
    border-radius: 3px; padding: 0px 10px; min-height: 48px; max-height: 48px;
    font-family: "Segoe UI"; font-size: 16px; font-weight: 400;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton:hover,
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton:focus {
    background: #15221a; border-color: #70c992;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton:pressed {
    background: #203427;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton#NewProjectCreate {
    color: #05250f; background: #32d26f; border: 1px solid #32d26f; font-weight: 700;
    min-width: 154px; max-width: 154px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton#NewProjectCreate:hover {
    background: #54e78a; border-color: #83efab;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton#NewProjectCreate:pressed {
    background: #28ae5b;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton#NewProjectCreate:disabled {
    background: #183b25; color: #788b7c; border-color: #31473a;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton#NewProjectFilmBrowse {
    background: #0a0e0e; border-color: #3a423d; min-width: 194px; max-width: 194px;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel#V3HomeActionTitle {
    font-size: 19px; font-weight: 400;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel#V3HomeActionBody {
    font-size: 14px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QPushButton#NewProjectCancel {
    min-width: 120px; max-width: 120px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"][compactIntake="true"] QLineEdit,
QDialog#V2NewProjectDialog[shellV3Dialog="true"][compactIntake="true"] QPushButton {
    min-height: 42px; max-height: 42px;
}
QDialog#V2NewProjectDialog[shellV3Dialog="true"] QLabel#NewProjectValidation {
    color: #e69a86; font-size: 13px;
}
"""

def stylesheet() -> str:
    return v2_stylesheet() + r"""

QMainWindow#TapeSiftV3 {
    background: #171815;
}

QFrame#V3WindowBezel {
    background: transparent;
    border: 1px solid rgba(80, 81, 71, 166);
    border-radius: 8px;
}

QFrame#V3WindowBezelBand {
    background: transparent;
    border: 4px solid #121410;
    border-radius: 7px;
}

QFrame#V3WindowBezelInner {
    background: transparent;
    border: 1px solid #090b09;
    border-radius: 5px;
}

QFrame#V3WindowBezel[bezelState="maximized"],
QFrame#V3WindowBezelBand[bezelState="maximized"],
QFrame#V3WindowBezelInner[bezelState="maximized"] {
    border-radius: 0px;
}

QWidget#TapeSiftV3ApplicationBar {
    background: #1a1b18;
    border-bottom: 1px solid #3b3b32;
    border-radius: 0px;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QMenuBar#CenteredApplicationMenuBar {
    color: #f0f2ef;
    font-family: "Segoe UI";
    font-size: 15px;
    font-weight: 500;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QMenuBar#CenteredApplicationMenuBar::item {
    padding: 10px 20px;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton#V3HomeNavigationChip {
    color: #f6f7f5;
    background-color: #242522;
    border: none;
    border-radius: 4px;
    padding: 0px;
    text-align: left;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 700;
    min-width: 102px;
    max-width: 102px;
    min-height: 38px;
    max-height: 38px;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[libraryEntry="true"] {
    min-width: 164px;
    max-width: 164px;
    min-height: 38px;
    max-height: 38px;
    padding: 0px;
    color: #f5f7f4;
    font-family: "Segoe UI";
    font-size: 16px;
    font-weight: 600;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton#ApplicationSettingsButton {
    color: #eef0ed;
    font-family: "Segoe UI";
    font-size: 15px;
    font-weight: 600;
    text-align: left;
    padding-left: 16px;
    padding-top: 0px;
    padding-bottom: 0px;
    min-height: 38px;
    max-height: 38px;
}

QFrame#V3ReviewWorkspace {
    background: #171815;
    border: none;
}

QFrame#V3LedgerRail,
QFrame#V3DetailsRail,
QFrame#V3OpenRail,
QFrame#V3CollapsedRail {
    background: #1a1b18;
    border: none;
    border-radius: 0px;
}

QFrame#V3LedgerRail {
    border-right: 1px solid #3b3b32;
}

QFrame#V3DetailsRail {
    border-left: 1px solid #3b3b32;
}

QFrame#V3RailCollapseStrip {
    background: #1f201c;
    border: none;
}

QToolButton#V3RailExpandButton,
QToolButton#V3RailCollapseButton {
    background: #1f201c;
    border: 1px solid #3b3b32;
    border-radius: 3px;
    padding: 0px;
}

QToolButton#V3RailExpandButton:hover,
QToolButton#V3RailCollapseButton:hover {
    background: #2b2b24;
    border-color: #565345;
}

QLabel#V3CollapsedRailTitle {
    color: #f2f5f2;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 10px;
    font-weight: 700;
}

QLabel#V3CollapsedRailSelection {
    color: #f2f5f2;
    background: #1f201c;
    border: 1px solid #39e07a;
    border-radius: 3px;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 12px;
    font-weight: 700;
    min-height: 26px;
}

QLabel#V3CollapsedRailCount,
QLabel#V3CollapsedRailFooter {
    color: #9d998b;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 8px;
    font-weight: 600;
}

/* Locked Iteration 2 Home states. The V2 screen remains the behavioral
   authority; these selectors are active only on the V3 rehost. */
#V2StartScreen[shellV3Home="true"] {
    background: qradialgradient(
        cx: 0.28, cy: 0.38, radius: 0.96,
        fx: 0.28, fy: 0.38,
        stop: 0 #1b1b1a,
        stop: 0.56 #181817,
        stop: 1 #111111
    );
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunLeft,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunDropHost,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningStart,
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeStatement {
    background: transparent;
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunLeft {
    background: qradialgradient(
        cx: 0.42, cy: 0.40, radius: 0.88,
        fx: 0.42, fy: 0.40,
        stop: 0 #1b1b1a,
        stop: 0.58 #191919,
        stop: 1 #181819
    );
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunDivider {
    background-color: #30312d;
    border: none;
}

#V2StartScreen[shellV3Home="true"] QLabel {
    font-family: "Segoe UI";
}

#V2StartScreen[shellV3Home="true"] QLabel#HomeWelcomeTitle {
    color: #f3f4f2;
    font-family: "Segoe UI";
    font-size: 25px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="roomSubtitle"] {
    color: #c5c7c3;
    font-family: "Segoe UI";
    font-size: 21px;
    font-weight: 400;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="eyebrow"] {
    font-family: "Segoe UI";
    font-size: 11px;
    font-weight: 700;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="projectMeta"] {
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunActions {
    background: qradialgradient(
        cx: 0.42, cy: 0.44, radius: 0.82,
        fx: 0.42, fy: 0.44,
        stop: 0 #1e1e1e,
        stop: 0.64 #1a1a1a,
        stop: 1 #181818
    );
    border: 1px solid #383838;
    border-radius: 16px;
}

#V2StartScreen[shellV3Home="true"] QPushButton[homeFirstRunAction="true"] {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0px;
}

#V2StartScreen[shellV3Home="true"] QPushButton[homeFirstRunAction="true"]:hover,
#V2StartScreen[shellV3Home="true"] QPushButton[homeFirstRunAction="true"]:focus {
    color: #ffffff;
    background-color: #20231f;
    border: 1px solid #39e07a;
}

#V2StartScreen[shellV3Home="true"] QLabel#V3HomeActionTitle {
    color: #f4f5f3;
    font-family: "Segoe UI";
    font-size: 16px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QLabel#V3HomeActionBody {
    color: #c5c7c3;
    font-family: "Segoe UI";
    font-size: 15px;
    font-weight: 400;
}

#V2StartScreen[shellV3Home="true"] QLabel#V3HomeActionChevron {
    color: #a8aaa6;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunActionRule {
    background-color: #30322c;
    border: none;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone {
    background: qradialgradient(
        cx: 0.50, cy: 0.46, radius: 0.72,
        fx: 0.50, fy: 0.46,
        stop: 0 #1b1b1b,
        stop: 0.55 #1a1a1a,
        stop: 1 #1a1a1a
    );
    border: 2px dashed rgba(91, 92, 85, 150);
    border-radius: 8px;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone > QWidget {
    background: transparent;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="dropHeading"] {
    color: #f5f6f4;
    font-family: "Segoe UI";
    font-size: 29px;
    font-weight: 600;
    letter-spacing: 0px;
}

#V2StartScreen[shellV3Home="true"] QLabel#V3HomeFormats {
    color: #b9bcb6;
    font-family: "Segoe UI";
    font-size: 18px;
    font-weight: 400;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone[dragover="true"] {
    background: qradialgradient(
        cx: 0.50, cy: 0.44, radius: 0.74,
        fx: 0.50, fy: 0.44,
        stop: 0 #30332f,
        stop: 0.58 #242a25,
        stop: 1 #1a1f1b
    );
    border: 1px solid #39e07a;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeDropZone[dragover="true"] QLabel[role="dropHeading"] {
    color: #39e07a;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="dropHeading"][dragover="true"] {
    color: #39e07a;
}

#V2StartScreen[shellV3Home="true"] QLabel[workflowNumber="true"] {
    font-family: "Segoe UI";
    font-size: 16px;
    font-weight: 500;
    color: #39e07a;
    border: 1px solid #2f6b45;
    border-radius: 30px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="workflowHeading"] {
    font-family: "Segoe UI";
    font-size: 19px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="subtle"] {
    font-family: "Segoe UI";
    font-size: 16px;
    font-weight: 400;
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeWorkflow QLabel[role="subtle"] {
    font-size: 16px;
    color: #dfe1dc;
}

#V2StartScreen[shellV3Home="true"] QLabel#V3HomeProjectDropHint {
    color: #8f938b;
    font-family: "Segoe UI";
    font-size: 19px;
    font-weight: 400;
    margin-top: 20px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="localStatus"],
#V2StartScreen[shellV3Home="true"] QLabel#HomeBuildLabel {
    font-family: "Consolas";
    font-size: 9px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="localStatus"] {
    letter-spacing: 0.65px;
}

#V2StartScreen[shellV3Home="true"] QLabel#HomeBuildLabel {
    letter-spacing: 0.2px;
}

#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects {
    background-color: rgba(25, 27, 24, 235);
    border: 1px solid #383a35;
    border-radius: 8px;
}

/* Segoe's taller line box needs a quieter, roomier recent-project rhythm. */
#V2StartScreen[shellV3Home="true"] QLabel[role="projectTitle"] {
    font-family: "Segoe UI";
    font-size: 18px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="projectSource"] {
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 400;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="projectMeta"] {
    font-family: "Segoe UI";
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0px;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="metricValue"],
#V2StartScreen[shellV3Home="true"] QLabel[role="metricAccent"] {
    font-family: "Segoe UI";
    font-size: 19px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QLabel[role="metricLabel"] {
    font-family: "Segoe UI";
    font-size: 8px;
    font-weight: 600;
    letter-spacing: 0.45px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton#V3HomeFirstRunNewProject[dropTarget="true"] {
    background: #1b241d;
    border: 1px solid #39e07a;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeAcceptedFile {
    background-color: #20211e;
    border: 1px solid #4d5149;
    border-radius: 5px;
    min-width: 400px;
    max-width: 520px;
}

#V2StartScreen[shellV3Home="true"] QLabel#V3HomeAcceptedFileName {
    color: #f2f5f2;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 14px;
    font-weight: 600;
}

#V2StartScreen[shellV3Home="true"] QLabel#V3HomeAcceptedCheck {
    color: #39e07a;
}

/* Locked Iteration 2 Library states. */
#V2LibraryScreen[shellV3Library="true"] {
    background: qradialgradient(
        cx: 0.50, cy: 0.05, radius: 1.20,
        fx: 0.50, fy: 0.05,
        stop: 0 #1b1b1b,
        stop: 0.62 #181818,
        stop: 1 #151515
    );
    color: #ededeb;
    font-family: "Segoe UI";
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryWorkbench,
#V2LibraryScreen[shellV3Library="true"] QFrame#V3LibraryLedgerEmpty {
    background: qradialgradient(
        cx: 0.50, cy: 0.18, radius: 1.08,
        fx: 0.50, fy: 0.18,
        stop: 0 #1d1d1d,
        stop: 1 #171717
    );
    border: 1px solid #3a3a38;
    border-radius: 1px;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryFilmPanel {
    background: transparent;
    border: none;
}

#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector {
    background: transparent;
    border-left: 1px solid #3a3a38;
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch {
    color: #d8d8d6;
    background-color: #1b1b1b;
    border: 1px solid #4a4a47;
    border-radius: 2px;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 400;
    padding-left: 11px;
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch:focus {
    background-color: #1d1d1d;
    border-color: #656561;
}

#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"] {
    color: #d6d6d3;
    background-color: #1b1b1b;
    border: 1px solid #3e3e3b;
    border-radius: 2px;
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 400;
    padding: 5px 9px;
}

#V2LibraryScreen[shellV3Library="true"]
QComboBox[libraryFilter="true"]::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    background: transparent;
    border: none;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryLedgerHeader {
    background-color: #171717;
    border: 1px solid #3a3a38;
    border-bottom: none;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V3LibraryEmptyInspector {
    background: transparent;
    border: none;
}

#V2LibraryScreen[shellV3Library="true"]
QFrame#V3LibraryEmptyInspectorDivider {
    color: #34362f;
    background-color: #34362f;
    border: none;
    min-height: 1px;
    max-height: 1px;
}

#V2LibraryScreen[shellV3Library="true"]
QLabel#V3LibraryEmptyInspectorPlaceholder {
    color: #777b75;
    background: transparent;
    border: none;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 400;
}

#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryEmptyInspectorCopy,
#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryEmptyInspectorHint,
#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryLedgerEmptyBody,
#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryLedgerEmptyHint {
    color: #9da29b;
    font-family: "Segoe UI";
    font-size: 14px;
    font-weight: 400;
}

#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryLedgerEmptyTitle {
    color: #f0f3f0;
    font-family: "Segoe UI";
    font-size: 19px;
    font-weight: 600;
}

#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryEmptyPreviewTitle {
    color: #eef2ee;
    font-family: "Segoe UI";
    font-size: 19px;
    font-weight: 600;
}

#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryEmptyPreviewCopy {
    color: #a1a69f;
    font-family: "Segoe UI";
    font-size: 15px;
    font-weight: 400;
}

#V2LibraryScreen[shellV3Library="true"] QLabel[role="ledgerColumn"] {
    color: #b9b9b6;
    font-family: "Rajdhani";
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.35px;
}

#V2LibraryScreen[shellV3Library="true"] QLabel[role="librarySection"] {
    color: #39e07a;
    font-family: "Segoe UI";
    font-size: 15px;
    font-weight: 700;
    letter-spacing: .7px;
}

#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryInspectorHeading"],
#V2LibraryScreen[shellV3Library="true"] QLabel[role="librarySubheading"] {
    color: #39e07a;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 700;
    letter-spacing: .7px;
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibraryClipTitle {
    color: #f1f1ef;
    background: transparent;
    border: none;
    border-radius: 0px;
    font-family: "Segoe UI";
    font-size: 24px;
    font-weight: 600;
    padding: 1px 0px;
}

#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryMeta"] {
    color: #a6a6a3;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 400;
}

#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryCount"] {
    color: #39e07a;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
}

#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryHint"] {
    color: #797b78;
    font-family: "Rajdhani";
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.25px;
}

#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryFilmGlyph,
#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibrarySearchGlyph,
#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryEmptyIcon {
    color: #666765;
    background: transparent;
    border: none;
    font-family: "Segoe MDL2 Assets";
}

#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryEmptyRebuild {
    min-height: 34px;
    color: #d8ddd8;
    background: #1a1a1a;
    border: 1px solid #249252;
    border-radius: 2px;
    font-family: "Segoe UI";
    font-size: 14px;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryEmptyRebuild:hover,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryEmptyRebuild:focus {
    color: #ffffff;
    background: #20251f;
    border-color: #39e07a;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBack,
#V2LibraryScreen[shellV3Library="true"] QToolButton#V3LibraryHome,
QPushButton#V3LibraryBack,
QToolButton#V3LibraryHome {
    background-color: #1b1c19;
    border: 1px solid #3b3c35;
    border-radius: 3px;
    padding: 0px;
    min-height: 32px;
    max-height: 32px;
}

#V2LibraryScreen[shellV3Library="true"]
QToolButton#V3LibraryFilterArrow {
    color: #a4a5a1;
    background: transparent;
    border: none;
    padding: 0px;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBack:hover,
#V2LibraryScreen[shellV3Library="true"] QToolButton#V3LibraryHome:hover,
QPushButton#V3LibraryBack:hover,
QToolButton#V3LibraryHome:hover {
    background-color: #23251f;
    border-color: #39e07a;
}

/* Opaque V3 dialog family. Existing forms remain the behavioral authority. */
QDialog#V3SettingsDialog,
QDialog#V3RecoveryDialog {
    background-color: #171815;
}

QFrame#V3SettingsBody,
QFrame#V3RecoverySummary {
    background-color: #1a1b18;
    border: 1px solid #3b3c35;
    border-radius: 4px;
}

QListWidget#V3SettingsNav {
    color: #b8bdb7;
    background-color: #181916;
    border: none;
    border-right: 1px solid #373830;
    padding: 8px 0px;
    outline: none;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 14px;
    font-weight: 600;
}

QListWidget#V3SettingsNav::item {
    min-height: 42px;
    padding-left: 14px;
    border-left: 3px solid transparent;
}

QListWidget#V3SettingsNav::item:selected {
    color: #eef3ef;
    background-color: #242620;
    border-left: 3px solid #39e07a;
}

QStackedWidget#V3SettingsStack {
    background-color: #1a1b18;
    border: none;
}

QFrame#V3RecoverySummary QLabel[role="recoveryKey"] {
    color: #8f958e;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 13px;
    font-weight: 600;
}

QFrame#V3RecoverySummary QLabel[role="recoveryValue"] {
    color: #e4e9e4;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 14px;
    font-weight: 600;
}

QPushButton#V3RecoveryDiscard {
    color: #ff6565;
    background-color: #191a17;
    border: 1px solid #c83b3b;
    min-height: 38px;
}

QPushButton#V3RecoveryReadOnly,
QPushButton#V3RecoveryRestore {
    min-height: 38px;
}

QFrame#V3LibraryNavDivider {
    color: #3a3b34;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton:disabled {
    color: #526057;
    background-color: #1a211b;
    border-color: #28332b;
}

#V2LibraryScreen[shellV3Library="true"]
QPushButton#V3LibraryClearSelection:disabled {
    color: #b1b3af;
    background: transparent;
    border: none;
}

#V2LibraryScreen[shellV3Library="true"]
QPushButton#V3LibraryBuildReel:disabled {
    color: #777a76;
    background-color: #242424;
    border: 1px solid #4a4a48;
    border-radius: 2px;
}

#V2LibraryScreen[shellV3Library="true"]
QPushButton#V3LibraryExport:disabled {
    color: #3d6049;
    background-color: #17351f;
    border: 1px solid #1b3b24;
    border-radius: 2px;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="library"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1d1d1d, stop:1 #171717
    );
    border-bottom: 1px solid #343432;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="library"]
QMenuBar#CenteredApplicationMenuBar {
    color: #eeeeec;
    font-family: "Segoe UI";
    font-size: 14px;
    font-weight: 400;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="library"]
QMenuBar#CenteredApplicationMenuBar::item {
    padding: 8px 21px;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="library"]
QLabel#LibrarySectionLabel {
    color: #39e07a;
    font-family: "Segoe UI";
    font-size: 14px;
    font-weight: 700;
    letter-spacing: .8px;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="library"]
QLabel#LibraryMastheadStats {
    color: #ddddda;
    font-family: "Segoe UI";
    font-size: 11px;
    font-weight: 500;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="library"]
QPushButton#V3LibraryRebuild {
    color: #39e07a;
    background-color: #181818;
    border: 1px solid #2f8d52;
    border-radius: 2px;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
}

QStackedWidget#V3ReviewCenterStack {
    background: #171815;
    border: none;
}

QFrame#V3ExportPage {
    background: #171815;
    border: none;
}

QPushButton#V3ExportBackToReview {
    color: #d9ddd9;
    background: #121410;
    border: 1px solid #3b3b32;
    border-radius: 3px;
    min-height: 27px;
    padding: 0px 12px;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 11px;
    font-weight: 650;
}

QPushButton#V3ExportBackToReview:hover,
QPushButton#V3ExportBackToReview:focus {
    color: #f2f5f2;
    background: #1f201c;
    border-color: #39e07a;
}

QFrame#V3ReviewActionMirror {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #2b2b24,
        stop:0.10 #25251f,
        stop:0.86 #171815,
        stop:1 #0c0d0a);
    border: 1px solid #565345;
    border-top-color: #7a7566;
    border-bottom-color: #0c0d0a;
    border-radius: 5px;
}

QFrame#V3ReviewActionMirror QPushButton {
    min-height: 38px;
    color: #e8dfc6;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #403d34,
        stop:0.12 #25251f,
        stop:0.78 #1f201c,
        stop:1 #0f100c);
    border: 1px solid #565345;
    border-top-color: #7a7566;
    border-bottom-color: #0c0d0a;
    border-radius: 4px;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 12px;
    font-weight: 650;
}

QFrame#V3ReviewActionMirror QPushButton:hover,
QFrame#V3ReviewActionMirror QPushButton:focus {
    color: #f2f5f2;
    background: #2b2b24;
    border-color: #8fd6a8;
}

QFrame#V3ReviewActionMirror QPushButton:pressed {
    background: #0f100c;
    border-color: #565345;
}

QFrame#V3ReviewActionMirror QPushButton[primary="true"] {
    color: #0f1710;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #62ec98,
        stop:0.18 #39e07a,
        stop:1 #25412b);
    border-color: #39e07a;
    border-top-color: #8fd6a8;
    border-bottom-color: #173722;
    font-weight: 750;
}

QFrame#V3ReviewActionMirror QPushButton:disabled {
    color: #647169;
    background: #1a1b18;
    border-color: #3b3b32;
}

/* Expanded Play Details gets the same locked action tray material and
   spacing as the collapsed mirror. The authoritative source buttons remain
   the same objects; this is presentation only. */
#V2ReviewInspector #InspectorActionBar {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #2b2b24,
        stop:0.10 #25251f,
        stop:0.86 #171815,
        stop:1 #0c0d0a);
    border-top: 1px solid #7a7566;
    border-bottom: 1px solid #0c0d0a;
}

#V2ReviewInspector #InspectorActionBar QPushButton {
    min-height: 34px;
    color: #e8dfc6;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #403d34,
        stop:0.12 #25251f,
        stop:0.78 #1f201c,
        stop:1 #0f100c);
    border: 1px solid #565345;
    border-top-color: #7a7566;
    border-bottom-color: #0c0d0a;
    border-radius: 4px;
    color: #e8dfc6;
    font-family: "Rajdhani", "Segoe UI";
    font-size: 11px;
    font-weight: 700;
}

#V2ReviewInspector #InspectorActionBar QPushButton:hover,
#V2ReviewInspector #InspectorActionBar QPushButton:focus {
    color: #f2f5f2;
    background: #2b2b24;
    border-color: #8fd6a8;
}

#V2ReviewInspector #InspectorActionBar QPushButton:pressed {
    background: #0f100c;
    border-color: #565345;
}

#V2ReviewInspector #InspectorActionBar QPushButton#InspectorSaveNext {
    color: #0f1710;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #62ec98,
        stop:0.18 #39e07a,
        stop:1 #25412b);
    border-color: #39e07a;
    border-top-color: #8fd6a8;
    border-bottom-color: #173722;
    font-weight: 750;
}

QWidget#V3TimelineLabelBand {
    background: #171815;
    border: none;
}

QFrame#V3TimelineZoomCluster {
    background: #111410;
    border: 1px solid #30372f;
    border-top-color: #444a41;
    border-bottom-color: #090b09;
    border-radius: 3px;
}

QLabel#V3TimelineZoomValue {
    color: #dbe5dd;
    background: transparent;
    border: none;
    font-family: "IBM Plex Mono Medium";
    font-size: 10px;
    font-weight: 500;
}

QFrame#V3TimelineZoomCluster QToolButton#TimelineZoomOut,
QFrame#V3TimelineZoomCluster QToolButton#TimelineZoomIn,
QFrame#V3TimelineZoomCluster QToolButton#TimelineFitPlay {
    color: #dce4de;
    background: #191c18;
    border: 1px solid #3e453c;
    border-top-color: #565c52;
    border-bottom-color: #11130f;
    border-radius: 3px;
    padding: 0px 5px;
    font-size: 9px;
    font-weight: 600;
}

QFrame#V3TimelineZoomCluster QToolButton#TimelineZoomOut:hover,
QFrame#V3TimelineZoomCluster QToolButton#TimelineZoomIn:hover,
QFrame#V3TimelineZoomCluster QToolButton#TimelineFitPlay:hover {
    color: #39e07a;
    background: #1b241d;
    border-color: #4b6552;
}

QFrame#V3TimelineZoomCluster QToolButton:disabled {
    color: #59625b;
    background: #121411;
    border-color: #2b302a;
}

QWidget#V3TagMapFooter {
    background: #171815;
    border: none;
}

/* Iteration 1 locked Review material. Home and Library are intentionally
   excluded: their separately approved Segoe-based references stay frozen. */
QFrame#V3ReviewWorkspace,
QFrame#V3ReviewRow,
QFrame#V3ReviewWorkspace QWidget {
    font-family: "IBM Plex Sans";
}

QFrame#V3ReviewWorkspace {
    color: #dfe5e0;
    background: #090c0a;
    border: none;
    border-radius: 0px;
}

QFrame#V3ReviewRow {
    background: #090c0a;
    border: none;
    border-bottom: 1px solid #050705;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="review"] {
    background: #101410;
    border-top: 1px solid #252c27;
    border-bottom: 1px solid #364039;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="review"]
QMenuBar#CenteredApplicationMenuBar,
QWidget#TapeSiftV3ApplicationBar[pageMode="review"] QLabel,
QWidget#TapeSiftV3ApplicationBar[pageMode="review"] QPushButton,
QWidget#TapeSiftV3ApplicationBar[pageMode="review"] QToolButton {
    font-family: "IBM Plex Sans";
}

QWidget#TapeSiftV3ApplicationBar[pageMode="review"]
QMenuBar#CenteredApplicationMenuBar {
    color: #e1e6e2;
    font-size: 12px;
    font-weight: 500;
}

QWidget#TapeSiftV3ApplicationBar[pageMode="review"]
QLabel#V3ReviewModeLabel {
    color: #7f8c82;
    font-family: "IBM Plex Mono Medium";
    font-size: 8px;
    font-weight: 500;
    letter-spacing: 1px;
}

QFrame#V3LedgerRail,
QFrame#V3DetailsRail,
QFrame#V3OpenRail,
QFrame#V3CollapsedRail {
    background: #101510;
}

QFrame#V3LedgerRail {
    border-right: 1px solid #3c483f;
}

QFrame#V3DetailsRail {
    border-left: 1px solid #3c483f;
}

QFrame#V3RailCollapseStrip {
    background: transparent;
    border: none;
}

QToolButton#V3RailExpandButton,
QToolButton#V3RailCollapseButton {
    background: #111712;
    border: 1px solid #3a443c;
    border-bottom-color: #59645b;
    border-radius: 3px;
}

QToolButton#V3RailExpandButton:hover,
QToolButton#V3RailCollapseButton:hover {
    background: #182019;
    border-color: #39e07a;
}

QLabel#V3CollapsedRailTitle,
QLabel#V3CollapsedRailCount,
QLabel#V3CollapsedRailFooter {
    font-family: "IBM Plex Sans";
    color: #99a49c;
    font-weight: 600;
}

QGraphicsView#V3CollapsedRailTitleView {
    background: transparent;
    border: none;
}

QLabel#V3CollapsedRailSelection {
    font-family: "IBM Plex Mono Medium";
    color: #d8e0da;
    background: #0a0e0b;
    border: 1px solid #315b40;
    border-radius: 3px;
}

QStackedWidget#V3ReviewCenterStack,
QWidget#V2ReviewPlayerPanel {
    background: #070907;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList,
QFrame#V3ReviewWorkspace #V2ReviewInspector {
    color: #dfe5e0;
    background: #101510;
    border: none;
}

QFrame#V3ReviewWorkspace #ClipLedgerHeader,
QFrame#V3ReviewWorkspace #InspectorHeader {
    background: #151b16;
    border-bottom: 1px solid #39433b;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QLabel[role="heading"],
QFrame#V3ReviewWorkspace #V2ReviewInspector QLabel[role="heading"] {
    color: #f1f4f1;
    font-family: "IBM Plex Sans";
    font-size: 14px;
    font-weight: 600;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QLineEdit,
QFrame#V3ReviewWorkspace #V2ReviewInspector QLineEdit,
QFrame#V3ReviewWorkspace #V2ReviewInspector QComboBox,
QFrame#V3ReviewWorkspace #V2ReviewInspector QPlainTextEdit {
    color: #d6ddd7;
    selection-color: #071009;
    selection-background-color: #39e07a;
    background: #0a0e0b;
    border: 1px solid #354038;
    border-bottom-color: #4b584e;
    border-radius: 3px;
    padding: 4px 7px;
    font-family: "IBM Plex Sans";
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QLineEdit:focus,
QFrame#V3ReviewWorkspace #V2ReviewInspector QLineEdit:focus,
QFrame#V3ReviewWorkspace #V2ReviewInspector QComboBox:focus,
QFrame#V3ReviewWorkspace #V2ReviewInspector QPlainTextEdit:focus {
    background: #0d130e;
    border-color: #39e07a;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QPushButton,
QFrame#V3ReviewWorkspace #V2ReviewClipList QToolButton {
    color: #cbd3cd;
    background: #111712;
    border: 1px solid #344038;
    border-bottom-color: #525e55;
    border-radius: 3px;
    font-family: "IBM Plex Sans";
    font-weight: 500;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QPushButton:hover,
QFrame#V3ReviewWorkspace #V2ReviewClipList QToolButton:hover {
    color: #f1f4f1;
    background: #182019;
    border-color: #5c6a60;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QPushButton:checked,
QFrame#V3ReviewWorkspace #V2ReviewClipList QToolButton[accent="true"] {
    color: #071009;
    background: #39e07a;
    border-color: #61e99a;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget {
    color: #ccd4ce;
    alternate-background-color: #101611;
    background: #0c110d;
    border: 1px solid #2f3932;
    gridline-color: #242d27;
    selection-background-color: #12361f;
    selection-color: #f0f5f1;
    font-family: "IBM Plex Sans";
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget::item {
    border-bottom: 1px solid #252e28;
    padding: 4px 6px;
}

QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget::item:selected {
    background: #143820;
    border-left: 3px solid #39e07a;
}

QFrame#V3ReviewWorkspace QFrame#V3ZeroLedgerBody {
    background: transparent;
    border: none;
}

QFrame#V3ReviewWorkspace QFrame#V3ZeroLedgerCard {
    background: #111612;
    border: 1px solid #303a33;
    border-top-color: #465148;
    border-bottom-color: #080b09;
    border-radius: 4px;
}

QFrame#V3ReviewWorkspace QLabel#V3ZeroLedgerIcon {
    background: transparent;
    border: none;
}

QFrame#V3ReviewWorkspace QLabel#V3ZeroLedgerTitle {
    color: #f0f4f1;
    font-family: "IBM Plex Sans";
    font-size: 14px;
    font-weight: 600;
}

QFrame#V3ReviewWorkspace QLabel#V3ZeroLedgerMessage {
    color: #8d9890;
    background: transparent;
    border: none;
    font-family: "IBM Plex Sans";
    font-size: 10px;
    font-weight: 400;
}

QFrame#V3ReviewWorkspace QPushButton#V3ZeroLedgerDetect {
    min-height: 34px;
    color: #e8d98a;
    background: #101511;
    border: 1px solid #665b2d;
    border-bottom-color: #332c15;
    border-radius: 3px;
    font-family: "IBM Plex Sans";
    font-size: 10px;
    font-weight: 600;
}

QFrame#V3ReviewWorkspace QPushButton#V3ZeroLedgerDetect:hover {
    color: #f2e7a4;
    background: #171a12;
    border-color: #8b7b37;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorSummaryCard,
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorPrimaryFields,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#InspectorPlayers,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#InspectorNotes,
QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox#ClipEditorDetails {
    background: #111612;
    border: 1px solid #303a33;
    border-top-color: #465148;
    border-radius: 4px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector QLabel#InspectorTitleSummary {
    color: #f0f4f1;
    font-family: "IBM Plex Sans";
    font-size: 15px;
    font-weight: 600;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector QLabel#InspectorRangeSummary,
QFrame#V3ReviewWorkspace #V2ReviewInspector QLabel#InspectorSituationSummary,
QFrame#V3ReviewWorkspace #V2ReviewInspector QLabel[role="subtle"] {
    color: #8e9a91;
    font-family: "IBM Plex Sans";
}

QFrame#V3ReviewWorkspace #V2ReviewInspector QGroupBox::title {
    color: #6fdc98;
    font-family: "IBM Plex Sans";
    font-weight: 600;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #ClipEditorEmptyState {
    background: #101510;
    border: none;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #ClipEditorEmptyIcon {
    color: #39e07a;
    background: #10261a;
    border: 1px solid #285b3b;
    border-radius: 4px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #ClipEditorEmptyKey {
    color: #d9dfda;
    background: #0a0e0b;
    border: 1px solid #38433b;
    border-bottom-color: #566158;
    border-radius: 3px;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector #ClipEditorEmptyHint {
    color: #aab4ac;
    font-family: "IBM Plex Sans";
    font-size: 10px;
}

QFrame#V3ReviewWorkspace #V2ReviewInspector
QToolButton#InspectorMoreDetailsToggle,
QFrame#V3ReviewWorkspace #V2ReviewInspector
QToolButton#InspectorAdvancedDetailsToggle {
    color: #d7ded8;
    background: #111612;
    border: 1px solid #303a33;
    border-bottom-color: #465148;
    border-radius: 3px;
    font-family: "IBM Plex Sans";
    font-weight: 500;
}

QFrame#V3ReviewActionMirror,
QFrame#V3ReviewWorkspace #InspectorActionBar {
    background: #121713;
    border: 1px solid #3b473e;
    border-top-color: #536158;
    border-bottom-color: #080b09;
    border-radius: 4px;
}

QFrame#V3ReviewActionMirror QPushButton,
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton {
    color: #d5ddd7;
    background: #0f1410;
    border: 1px solid #344038;
    border-bottom-color: #566158;
    border-radius: 3px;
    font-family: "IBM Plex Sans";
    font-size: 10px;
    font-weight: 600;
}

QFrame#V3ReviewActionMirror QPushButton:hover,
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton:hover {
    color: #f1f5f2;
    background: #172019;
    border-color: #62a77a;
}

QFrame#V3ReviewActionMirror QPushButton[primary="true"],
QFrame#V3ReviewWorkspace #InspectorActionBar
QPushButton#InspectorSaveNext {
    color: #061108;
    background: #39e07a;
    border-color: #69eba0;
    border-bottom-color: #1a843f;
}

QFrame#V3ReviewActionMirror QPushButton:disabled,
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton:disabled {
    color: #59625b;
    background: #0d120e;
    border-color: #29312b;
}

QFrame#V3ZeroPlayOverlay {
    background: #070907;
    border: none;
}

QFrame#V3ZeroPlayCard {
    background: #101510;
    border: 1px solid #354038;
    border-top-color: #536158;
    border-bottom-color: #080b09;
    border-radius: 4px;
}

QLabel#V3ZeroPlayIcon {
    background: #10261a;
    border: 1px solid #285b3b;
    border-radius: 4px;
}

QLabel#V3ZeroPlayTitle {
    color: #f1f4f1;
    font-size: 22px;
    font-weight: 600;
}

QLabel#V3ZeroPlayCopy {
    color: #9da8a0;
    font-size: 11px;
}

QPushButton#V3ZeroDetectPlays,
QPushButton#V3ZeroNewClip {
    min-height: 40px;
    color: #dbe2dc;
    background: #111712;
    border: 1px solid #3a463d;
    border-bottom-color: #5c695f;
    border-radius: 3px;
    padding: 0px 18px;
    font-family: "IBM Plex Sans";
    font-weight: 600;
}

QPushButton#V3ZeroDetectPlays {
    color: #061108;
    background: #39e07a;
    border-color: #6ceca2;
    border-bottom-color: #1b873f;
}

QLabel#V3ZeroShortcutKey {
    color: #d9dfda;
    background: #0a0e0b;
    border: 1px solid #38433b;
    border-bottom-color: #566158;
    border-radius: 3px;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
    font-weight: 500;
}

QLabel#V3ZeroShortcutLabel,
QLabel#V3ZeroShortcutDivider {
    color: #8d9890;
    font-size: 9px;
}

QLabel#V3ZeroPrivacy {
    color: #667169;
    font-family: "IBM Plex Sans";
    font-size: 9px;
    font-weight: 500;
}

QFrame#V3ReviewFooter {
    background: #0b100c;
    border: none;
    border-top: 1px solid #566158;
    border-bottom: 2px solid #030503;
}

QFrame#V3ReviewFooterLeft,
QFrame#V3ReviewFooterCenter,
QFrame#V3ReviewFooterRight {
    background: #0d120e;
    border: none;
}

QFrame#V3ReviewFooterLeft {
    border-right: 1px solid #2b352e;
}

QFrame#V3ReviewFooterRight {
    border-left: 1px solid #2b352e;
}

QFrame#V3ReviewFooterIndicator {
    background: #39e07a;
    border: 1px solid #12351e;
    border-radius: 3px;
}

QLabel#V3ReviewFooterLocal,
QLabel#V3ReviewFooterCount {
    color: #91a097;
    font-family: "IBM Plex Sans";
    font-size: 9px;
    font-weight: 500;
}

QLabel#V3ReviewFooterShortcuts {
    color: #aeb8b0;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
    font-weight: 500;
}

QLabel#V3ReviewFooterCount {
    color: #85e7aa;
    font-weight: 600;
}

QFrame#V3ReviewWorkspace #DockV2Timecode,
QFrame#V3ReviewWorkspace #DockV2FrameCounter,
QFrame#V3ReviewWorkspace #DockV2InOut,
QFrame#V3ReviewWorkspace #DockV2InOutOut,
QFrame#V3ReviewWorkspace #InspectorRangeDuration,
QFrame#V3ReviewWorkspace QLineEdit#InspectorToGoEdit {
    font-family: "IBM Plex Mono Medium";
}

/* Iteration 2: raised Ledger and Play Details modules. */
QFrame#V3ReviewWorkspace #ClipLedgerHeader,
QFrame#V3ReviewWorkspace #InspectorHeader {
    background: #151b16;
    border-top: 1px solid #465148;
    border-bottom: 1px solid #080b09;
}

QFrame#V3ReviewWorkspace #ClipLedgerHeader {
    min-height: 40px;
    max-height: 40px;
    padding: 0px 40px 0px 14px;
}

QFrame#V3ReviewWorkspace #InspectorHeader {
    min-height: 48px;
    padding: 0px;
}

QFrame#V3ReviewStatusBadge {
    min-width: 70px;
    max-width: 70px;
    min-height: 26px;
    max-height: 26px;
    color: #8df0b0;
    background: #10261a;
    border: 1px solid #285b3b;
    border-radius: 3px;
    padding: 0px;
}

QFrame#V3ReviewStatusBadge #InspectorReviewState {
    min-width: 0px;
    min-height: 24px;
    max-height: 24px;
    color: #8df0b0;
    background: transparent;
    border: none;
    padding: 0px;
    font-family: "IBM Plex Mono Medium";
    font-size: 8px;
    font-weight: 500;
}

QLabel#V3ReviewStatusIcon,
QLabel#V3LedgerGroupChevron,
QLabel#V3LedgerProjectArrow {
    background: transparent;
    border: none;
}

QFrame#V3ReviewWorkspace #InspectorSummaryCard {
    margin: 10px 10px 5px 10px;
    background: #151b16;
    border: 1px solid #3a463d;
    border-top-color: #566259;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QFrame#V3ReviewWorkspace #InspectorSummaryCard QLabel#InspectorRangeSummary {
    color: #aab5ac;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
    padding-top: 4px;
}

QFrame#V3ReviewWorkspace #InspectorPrimaryFields,
QFrame#V3ReviewWorkspace QGroupBox#InspectorPlayers,
QFrame#V3ReviewWorkspace QGroupBox#InspectorNotes,
QFrame#V3ReviewWorkspace QGroupBox#ClipEditorDetails {
    margin: 5px 10px;
    padding: 12px 8px 8px 8px;
    background: #121713;
    border: 1px solid #354038;
    border-top-color: #4b584e;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QFrame#V3ReviewWorkspace #InspectorPrimaryFields {
    background: transparent;
    border: none;
    margin: 0px;
    padding: 0px;
}

QFrame#V3RangeCard {
    margin: 5px 10px;
    background: #121713;
    border: 1px solid #354038;
    border-top-color: #4b584e;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QLabel#V3RangeTitle {
    color: #e0e7e1;
    font-family: "IBM Plex Sans";
    font-size: 11px;
    font-weight: 600;
}

QLabel#V3RangeCaption {
    color: #75827a;
    font-family: "IBM Plex Sans";
    font-size: 8px;
}

QPushButton#V3EditPoints {
    color: #71e39c;
    background: transparent;
    border: none;
    padding: 0px;
    font-size: 9px;
    font-weight: 500;
}

QFrame#V3RangeCard QLabel#InspectorRangeDuration {
    min-height: 29px;
    color: #d6ddd7;
    background: #0a0e0b;
    border: 1px solid #354038;
    border-bottom-color: #4b584e;
    border-radius: 3px;
    qproperty-alignment: AlignCenter;
}

QFrame#V3RangeCard QLineEdit#V3RangeStart,
QFrame#V3RangeCard QLineEdit#V3RangeEnd {
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
    font-weight: 500;
}

QFrame#V3ReviewWorkspace QGroupBox#InspectorPlayers,
QFrame#V3ReviewWorkspace QGroupBox#InspectorNotes {
    padding-top: 30px;
}

QFrame#V3ReviewWorkspace QGroupBox#InspectorPlayers::title,
QFrame#V3ReviewWorkspace QGroupBox#InspectorNotes::title {
    subcontrol-origin: border;
    subcontrol-position: top left;
    left: 9px;
    top: 9px;
    padding: 0px;
    color: #dfe6e0;
    font-family: "IBM Plex Sans";
    font-size: 10px;
    font-weight: 600;
}

QFrame#V3ReviewWorkspace #InspectorActionBar {
    margin: 8px 10px 10px 10px;
    padding: 6px;
    background: #151b16;
    border: 1px solid #3a463d;
    border-top-color: #566259;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QFrame#V3PlayActionsHeader {
    background: transparent;
    border: none;
}

QFrame#V3PlayActionsHeader QLabel {
    color: #dfe6e0;
    font-family: "IBM Plex Sans";
    font-size: 10px;
    font-weight: 600;
}

QLabel#V3PlayActionsState {
    color: #75e49e;
    font-size: 9px;
    font-weight: 500;
}

QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton {
    min-height: 38px;
    padding: 0px 9px;
    background: #0f1410;
    border: 1px solid #3a463d;
    border-bottom-color: #59655c;
    border-radius: 3px;
    text-align: left;
}

QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton::menu-indicator,
QFrame#V3LedgerCommandCard #ClipLedgerHeaderActions
QToolButton::menu-indicator {
    image: none;
    width: 0px;
}

QFrame#V3ReviewWorkspace #InspectorActionBar
QPushButton#InspectorSaveNext {
    color: #061108;
    background: #39e07a;
    border-color: #6ceca2;
    border-bottom-color: #1a843f;
}

/* Iteration 2: separate Clip Export and Package / Cut Up routes. */
QFrame#V3ExportPage,
QFrame#V3ExportPage QWidget {
    color: #dce5de;
    background: #0b0f0c;
    border-radius: 0px;
    font-family: "IBM Plex Sans";
}

QLabel#V3ExportTitle {
    color: #f2f5f2;
    font-size: 16px;
    font-weight: 600;
}

QLabel#V3ExportSubtitle,
QLabel#V3ExportQuiet {
    color: #89958c;
    font-size: 10px;
}

QPushButton#V3ExportBackToReview,
QPushButton#V3ExportCancel {
    min-height: 30px;
    padding: 0px 12px;
    color: #cfd8d1;
    background: #111712;
    border: 1px solid #3a463d;
    border-bottom-color: #59655c;
    border-radius: 3px;
}

QLabel#V3ExportStageCell {
    min-height: 34px;
    padding-left: 12px;
    color: #7e8a81;
    background: #101510;
    border: 1px solid #2f3932;
    border-bottom: 2px solid #242d27;
    border-radius: 3px;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
}

QLabel#V3ExportStageCell[active="true"] {
    color: #8df0b0;
    background: #121a14;
    border-color: #315b40;
    border-bottom: 2px solid #39e07a;
}

QFrame#V3ExportCard,
QFrame#V3ExportQueueCard {
    background: #151b16;
    border: 1px solid #39443c;
    border-top-color: #566259;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QLabel#V3ExportCardTitle {
    color: #f0f4f1;
    font-size: 12px;
    font-weight: 600;
}

QLabel#V3ExportCaption,
QLabel#V3ExportQueueBadge {
    color: #6fdc98;
    font-family: "IBM Plex Mono Medium";
    font-size: 8px;
    font-weight: 500;
}

QFrame#V3ExportPage QComboBox,
QFrame#V3ExportPage QCheckBox {
    min-height: 32px;
    color: #d6ddd7;
    background: #0a0e0b;
    border: 1px solid #354038;
    border-bottom-color: #4b584e;
    border-radius: 3px;
    padding: 0px 8px;
}

QLabel#V3ExportDestination {
    min-height: 30px;
    padding: 0px 9px;
    color: #b9c4bb;
    background: #0a0e0b;
    border: 1px solid #303a33;
    border-radius: 3px;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
}

QLabel#V3ExportReady {
    color: #8df0b0;
    font-size: 10px;
    font-weight: 500;
}

QPushButton#V3ExportStart,
QPushButton#V3PackageStart {
    min-height: 34px;
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-bottom-color: #1a843f;
    border-radius: 3px;
    padding: 0px 14px;
    font-weight: 600;
}

QPushButton#V3ExportStart[ready="true"],
QPushButton#V3PackageStart[ready="true"] {
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-bottom-color: #1a843f;
}

QPushButton#V3ExportStart:disabled,
QPushButton#V3PackageStart:disabled {
    color: #59625b;
    background: #0d120e;
    border-color: #29312b;
}

QFrame#V3ExportQueueLane,
QFrame#V3PackagePlayRow {
    background: #101510;
    border: 1px solid #2f3932;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QLabel#V3PackageGrip {
    background: transparent;
    border: none;
}

QLabel#V3ExportLaneIcon {
    background: #0d1610;
    border: 1px solid #315b40;
    border-radius: 3px;
}

QLabel#V3PackageThumbnail {
    background: #080b09;
    border: 1px solid #303a33;
    border-radius: 2px;
}

QPushButton#V3PackageRemove {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 0px;
}

QPushButton#V3PackageRemove:hover {
    background: #1b241d;
    border-color: #4a574d;
}

QPushButton#V3PackageRemove:disabled {
    background: transparent;
    border-color: transparent;
}

QLabel#V3ExportQueueTitle,
QLabel#V3PackagePlayCopy {
    color: #dce4de;
    font-weight: 500;
}

QLabel#V3ExportQueueState,
QLabel#V3ExportCompleteState,
QLabel#V3PackagePlayNumber,
QLabel#V3PackageMore {
    color: #8df0b0;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
}

QLabel#V3ExportCompletePath {
    color: #aab5ac;
    background: transparent;
    border: none;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
}

QFrame#V3ExportQueueLane[completeLane="true"] QLabel#V3ExportQueueTitle,
QLabel#V3ExportCompleteDetail {
    color: #39e07a;
}

QWidget#V3ExportCompleteActions {
    background: transparent;
    border: none;
}

QPushButton#V3ExportOpenOutput,
QPushButton#V3ExportShowFolder {
    min-height: 32px;
    max-height: 32px;
    padding: 0px 11px;
    color: #dce4de;
    background: #151a15;
    border: 1px solid #3b463d;
    border-bottom-color: #0a0d0b;
    border-radius: 3px;
    font-weight: 550;
}

QPushButton#V3ExportOpenOutput:hover,
QPushButton#V3ExportShowFolder:hover {
    color: #8df0b0;
    background: #1b241d;
    border-color: #4a7256;
}

QLabel#V3PackageTotal {
    color: #aab5ac;
    font-family: "IBM Plex Mono Medium";
    font-size: 9px;
    font-weight: 500;
}

QProgressBar#V3ExportProgress {
    min-height: 6px;
    max-height: 6px;
    background: #080b09;
    border: 1px solid #29312b;
    border-radius: 2px;
}

QProgressBar#V3ExportProgress::chunk {
    background: #39e07a;
    border-radius: 1px;
}

QFrame#V3LedgerProjectCard {
    min-height: 46px;
    margin: 0px 10px 7px 10px;
    background: #151b16;
    border: 1px solid #39443c;
    border-top-color: #566259;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QLabel#V3LedgerProjectName {
    color: #eef3ef;
    font-size: 10px;
    font-weight: 600;
}

QLabel#V3LedgerProjectState,
QLabel#V3LedgerProjectArrow,
QLabel#V3LedgerFooterOrder {
    color: #819087;
    font-size: 9px;
}

QFrame#V3LedgerCommandCard {
    margin: 0px 10px 7px 10px;
    background: #121713;
    border: 1px solid #354038;
    border-top-color: #4b584e;
    border-bottom-color: #080b09;
    border-radius: 3px;
}

QFrame#V3LedgerCommandCard #ClipLedgerHeaderActions {
    min-height: 36px;
    background: transparent;
}

QFrame#V3LedgerCommandCard QPushButton,
QFrame#V3LedgerCommandCard QToolButton {
    min-height: 32px;
    padding: 0px 8px;
}

QFrame#V3LedgerCommandCard QLineEdit {
    min-height: 31px;
}

QWidget#V3LedgerFilters {
    min-height: 36px;
    max-height: 36px;
    background: transparent;
}

QFrame#V3ReviewWorkspace QWidget#V3LedgerFilters QPushButton {
    min-height: 32px;
    max-height: 32px;
    font-size: 10px;
    padding: 0px 6px;
}

QFrame#V3LedgerFooter {
    min-height: 28px;
    margin: 0px 10px 8px 10px;
    background: #101510;
    border: 1px solid #303a33;
    border-top: none;
    border-radius: 0px 0px 3px 3px;
}

QFrame#V3LedgerGroupHeader {
    min-height: 34px;
    max-height: 34px;
    margin: 0px 10px;
    background: #121713;
    border: 1px solid #354038;
    border-bottom-color: #080b09;
    border-radius: 3px 3px 0px 0px;
}

QLabel#V3LedgerGroupName {
    color: #eef3ef;
    font-size: 10px;
    font-weight: 600;
}

QLabel#V3LedgerGroupCount {
    color: #8d9990;
    font-size: 9px;
}

QLabel#V3LedgerGroupLogged {
    color: #78e4a1;
    font-size: 9px;
    font-weight: 600;
}

QLabel#V3LedgerReviewQueue {
    color: #8d9990;
    background: transparent;
    border: none;
    font-size: 9px;
}

QLabel#V3LedgerFooterReady {
    color: #8df0b0;
    font-size: 9px;
    font-weight: 500;
}

QLabel#V3DetailsTitle {
    color: #f1f4f1;
    font-size: 14px;
    font-weight: 600;
}

QLabel#V3DetailsSubtitle {
    color: #819087;
    font-size: 9px;
    font-weight: 400;
}

QLabel#V3PlayCallChip {
    min-width: 44px;
    min-height: 24px;
    color: #061108;
    background: #39e07a;
    border: 1px solid #6ceca2;
    border-radius: 3px;
    padding: 0px 8px;
    font-size: 9px;
    font-weight: 600;
}

QLabel#ClipLedgerProgress {
    color: #8df0b0;
    font-size: 9px;
    font-weight: 500;
}

/* Approved layered chassis: major regions separate by tone and recessed
   seams, never by the bright full-span rules that flattened Review. */
QWidget#TapeSiftV3ApplicationBar[pageMode="review"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #21211f,
        stop:0.14 #1d1d1b,
        stop:0.78 #171716,
        stop:1 #111110);
    border: none;
    border-bottom: 1px solid #050505;
}

QFrame#V3ReviewWorkspace {
    background: #070807;
    border: none;
}

QFrame#V3ReviewRow {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #151514,
        stop:0.08 #10100f,
        stop:0.92 #0b0b0b,
        stop:1 #070807);
    border: none;
    border-bottom: 1px solid #020202;
}

QStackedWidget#V3ReviewCenterStack {
    background: #0a0b0a;
    border: none;
}

QWidget#V2ReviewPlayerPanel {
    background: #0d0e0d;
    border: none;
}

QFrame#V3LedgerRail,
QFrame#V3DetailsRail,
QFrame#V3OpenRail,
QFrame#V3CollapsedRail {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1d1d1b,
        stop:0.16 #1a1a19,
        stop:0.72 #151514,
        stop:1 #10100f);
    border: none;
}

QFrame#V3LedgerRail {
    border-right: 1px solid #050505;
}

QFrame#V3DetailsRail {
    border-left: 1px solid #050505;
}

QFrame#V3RailCollapseStrip {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #20201e,
        stop:0.42 #191918,
        stop:1 #111110);
    border: none;
}

QWidget#V3TimelineLabelBand,
QWidget#PlayerStripSlot {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1b1b19,
        stop:0.16 #141413,
        stop:1 #090a09);
    border: none;
}

#TagMapHeader {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #20201e,
        stop:0.12 #1a1a19,
        stop:0.82 #131312,
        stop:1 #0b0b0b);
    border: none;
    border-top: 1px solid #101010;
    border-bottom: 1px solid #050505;
    border-radius: 0px;
}

#V2AttributeGrid {
    background: #0f0f0f;
    border: none;
    border-top: 1px solid #1e1e1d;
    border-radius: 0px;
}

QFrame#V3ReviewFooter {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1f1f1d,
        stop:0.10 #1b1b1a,
        stop:0.84 #151514,
        stop:1 #0d0d0d);
    border: none;
    border-top: 1px solid #2c2d2a;
    border-bottom: 2px solid #050505;
}

QFrame#V3ReviewFooterLeft,
QFrame#V3ReviewFooterCenter,
QFrame#V3ReviewFooterRight {
    background: transparent;
    border: none;
}

/* Approved Home and Library material pass. Geometry and behavior stay owned
   by their existing V3 screens; this only carries the layered Review chassis
   across the remaining primary surfaces. */
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #242422,
        stop:0.12 #20201f,
        stop:0.76 #191918,
        stop:1 #111110);
    border: none;
    border-bottom: 1px solid #070707;
}

/* Home separates its regions with tone, never with rules. The bar falls off
   into the page ground instead of terminating on a hairline. */
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #262624,
        stop:0.14 #232321,
        stop:0.72 #1d1d1c,
        stop:1 #191918);
    border: none;
}

/* Deeper toward the center canvas, lifting toward the outer surfaces. */
#V2StartScreen[shellV3Home="true"] {
    background: qradialgradient(
        cx: 0.52, cy: 0.40, radius: 0.98,
        fx: 0.52, fy: 0.40,
        stop: 0 #151514,
        stop: 0.50 #181817,
        stop: 1 #1c1c1b
    );
}

/* Broad brightness plateau on the left that dissolves into the canvas. */
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunLeft {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #212120,
        stop:0.62 #1e1e1d,
        stop:0.90 rgba(27, 27, 26, 90),
        stop:1 rgba(27, 27, 26, 0));
    border: none;
}

#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunActions {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #292927,
        stop:0.45 #232322,
        stop:1 #1c1c1b);
    border: none;
    border-radius: 8px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
}

#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"]:hover,
#V2StartScreen[shellV3Home="true"]
QPushButton[homeFirstRunAction="true"]:focus {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #2b2d29,
        stop:1 #1d211d);
    border: 1px solid #286642;
}

/* The structural rule frames stay in the layout for geometry, but they carry
   no paint: region separation is whitespace and tone, not hairlines. */
#V2StartScreen[shellV3Home="true"] QFrame#V3HomeFirstRunActionRule,
#V2StartScreen[shellV3Home="true"] QFrame[homeRule="true"],
#V2StartScreen[shellV3Home="true"] QFrame[workflowConnector="true"],
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFirstRunDivider {
    background: transparent;
    border: none;
}

/* One shared raised surface; the projects read as content floating on it. */
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #242423,
        stop:0.30 #212120,
        stop:1 #1b1b1a);
    border: none;
    border-radius: 10px;
}

/* The recent list scrolls on the panel's own plane; its viewport must not
   repaint the window ground over that raised surface. */
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects QScrollArea,
#V2StartScreen[shellV3Home="true"]
QWidget#V3HomeReturningProjects QScrollArea > QWidget,
#V2StartScreen[shellV3Home="true"]
QWidget#V3HomeReturningProjects QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}

#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"] {
    background: transparent;
    border: none;
    border-radius: 6px;
}

/* The row's plain column hosts (progress, updated) are structure, not
   surfaces - they must not punch the window ground through the panel. */
#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"] > .QWidget {
    background: transparent;
    border: none;
}

#V2StartScreen[shellV3Home="true"] QFrame[projectCard="true"]:hover {
    background: rgba(255, 255, 255, 12);
    border: none;
    border-radius: 6px;
}

#V2StartScreen[shellV3Home="true"] QLabel[projectThumb="true"] {
    background: #0d0e0d;
    border: 1px solid #0a0a0a;
    border-top: 1px solid #30302e;
    border-radius: 3px;
}

/* A contained plinth: raised off the ground and rounded, so it reads as its
   own plane rather than a ruled band running off the bottom edge. */
#V2StartScreen[shellV3Home="true"] QWidget#V3HomeWorkflow {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #272725,
        stop:0.45 #232322,
        stop:1 #1e1e1d);
    border: none;
    border-radius: 10px;
}

#V2LibraryScreen[shellV3Library="true"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1c1c1b,
        stop:0.54 #171716,
        stop:1 #111110);
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch,
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #292927,
        stop:0.12 #242422,
        stop:1 #191918);
    border: 1px solid #101010;
    border-top: 1px solid #3a3a37;
    border-bottom: 1px solid #080808;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch:focus,
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"]:focus,
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"]:focus {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #30302d,
        stop:1 #1d211d);
    border-top-color: #4a4a46;
    border-left-color: #286642;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryWorkbench {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #252523,
        stop:0.08 #20201f,
        stop:0.74 #181817,
        stop:1 #121212);
    border: 1px solid #090909;
    border-top: 1px solid #383835;
    border-bottom: 1px solid #060606;
    border-radius: 4px;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryFilmPanel {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #101110,
        stop:0.12 #0d0e0d,
        stop:1 #090a09);
    border: 1px solid #080808;
    border-top: 1px solid #252524;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"]
QStackedWidget#V2LibraryPreview,
#V2LibraryScreen[shellV3Library="true"] QLabel#V2LibraryPreview {
    background: #090a09;
    border: none;
    border-bottom: 1px solid #030303;
    border-radius: 2px;
}

#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #292927,
        stop:0.08 #232321,
        stop:0.74 #1d1d1c,
        stop:1 #181817);
    border: 1px solid #101010;
    border-top: 1px solid #3b3b38;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryLedgerHeader {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #252523,
        stop:0.14 #20201f,
        stop:1 #151515);
    border: 1px solid #090909;
    border-top: 1px solid #353532;
    border-bottom: 1px solid #070707;
}

#V2LibraryScreen[shellV3Library="true"] QListWidget#V2LibraryResults {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #181817,
        stop:1 #101110);
    alternate-background-color: #171816;
    border: 1px solid #090909;
    border-top: none;
    border-radius: 0px 0px 3px 3px;
    outline: none;
}

#V2LibraryScreen[shellV3Library="true"]
QListWidget#V2LibraryResults::item:hover {
    background-color: #222521;
}

#V2LibraryScreen[shellV3Library="true"]
QListWidget#V2LibraryResults::item:selected {
    background-color: #183f28;
    border-top: 1px solid #2b6843;
    border-bottom: 1px solid #0c2415;
}

#V2LibraryScreen[shellV3Library="true"] QFrame#V3LibraryLedgerEmpty {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #222220,
        stop:0.08 #1d1d1c,
        stop:0.76 #171716,
        stop:1 #111110);
    border: 1px solid #090909;
    border-top: 1px solid #353532;
    border-bottom: 1px solid #060606;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryEmptyRebuild,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBuildReel,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryExport,
#V2LibraryScreen[shellV3Library="true"] QPushButton[projectAction="true"],
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton#V3LibraryRebuild,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QPushButton#V3LibraryBack,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QToolButton#V3LibraryHome {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #2d2d2a,
        stop:0.10 #262624,
        stop:0.78 #1b1b1a,
        stop:1 #121212);
    border: 1px solid #0a0a0a;
    border-top: 1px solid #464642;
    border-bottom: 1px solid #060606;
    border-radius: 3px;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryExport {
    color: #07130b;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #54ea8d,
        stop:0.12 #39e07a,
        stop:1 #24bf62);
    border-top-color: #74f0a4;
    border-bottom-color: #13743a;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton:disabled {
    color: #676b67;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #242422,
        stop:1 #181817);
    border: 1px solid #101010;
    border-top: 1px solid #30302d;
}

#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryExport:disabled {
    color: #355440;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #193522,
        stop:1 #102416);
    border-top-color: #244b31;
}

/* Rebuild stays a deliberate green-outline action in both empty-state and
   application-bar placements; keep the bevel without muting its meaning. */
#V2LibraryScreen[shellV3Library="true"]
QPushButton#V3LibraryEmptyRebuild,
QWidget#TapeSiftV3ApplicationBar[pageMode="library"]
QPushButton#V3LibraryRebuild {
    color: #6ce99a;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #292927,
        stop:0.10 #232321,
        stop:1 #171817);
    border: 1px solid #25864d;
    border-top-color: #3eaa68;
    border-bottom-color: #124a29;
    border-radius: 3px;
}
""" + V3_ANODIZED_FIELD_CONSOLE_MATERIAL + V3_OPTION1_HOME_MATERIAL + V3_FLAT_MATERIAL + V3_HOME_INTAKE_MATERIAL + V3_LIBRARY_PAGEBOOK + V3_REVIEW_PAGEBOOK + V3_TAG_MAP_MATERIAL + V3_DETAILS_PAGEBOOK + V3_DETECT_PAGEBOOK + V3_HEATMAP_PAGEBOOK + V3_MEDIA_EXPORT_PAGEBOOK + V3_SUPPORT_PAGEBOOK + V3_TITANIUM_REVIEW_MATERIAL + V3_NEUTRAL_REFRESH + V3_BROADCAST_SLATE + V3_FILM_ROOM


V3_NEUTRAL_REFRESH = """
QWidget#TapeSiftV3ApplicationBar[pageMode="home"], QWidget#TapeSiftV3ApplicationBar[pageMode="library"] {background:#0a0b0c;border-bottom:1px solid #262a2f;}
#V3HomeTapeSiftName {background:transparent;border:0;padding:0;}
QWidget#TapeSiftV3ApplicationBar #LibrarySectionLabel {color:#39e07a;font:600 15px 'Segoe UI';border-bottom:2px solid #39e07a;padding:8px;}
QWidget#TapeSiftV3ApplicationBar #V3LibraryHome, QWidget#TapeSiftV3ApplicationBar #V3LibrarySettings {background:transparent;border:0;font:15px 'Segoe UI';}
#V2StartScreen QWidget, #V2StartScreen QScrollArea {background:transparent;}
#V2LibraryScreen #V3LibrarySelectedSummary {background:transparent;border:0;}
QMainWindow#TapeSiftV3, #V3ReviewWorkspace, #V3ReviewRow,
#V3LedgerRail, #V3DetailsRail, #V3OpenRail, #V3CollapsedRail,
#V2ReviewClipList, #V2ReviewInspector {background:#111214;border-color:#262a2f;}
#V3ReviewFooter, #V3ReviewFooterLeft, #V3ReviewFooterCenter, #V3ReviewFooterRight,
#V3TagMapTools, #V3TagMapLegend {background:#111214;border-color:#262a2f;}
#ClipLedgerHeader, #InspectorHeader, #V3LedgerGroupHeader {background:#16181b;border-color:#262a2f;}
#V3ReviewWorkspace QHeaderView::section {background:#16181b;border-color:#262a2f;color:#e6e8e6;}
#V3ReviewWorkspace QLineEdit, #V3ReviewWorkspace QComboBox {background:#0d0e10;border-color:#262a2f;color:#e6e8e6;}
#V3ReviewWorkspace QPushButton, #V3ReviewWorkspace QToolButton {background:#111214;border-color:#262a2f;color:#e6e8e6;}
#V3ReviewWorkspace QPushButton:hover, #V3ReviewWorkspace QToolButton:hover {background:#1c1f23;border-color:#3a4046;}
#V3ReviewWorkspace QPushButton:checked {background:#163a26;border-color:#1d7a45;color:#c9f2d7;}
#V3ReviewWorkspace QPushButton[primary="true"] {background:#39e07a;color:#062312;border-color:#39e07a;}
#V3ReviewWorkspace QPushButton:focus, #V3ReviewWorkspace QToolButton:focus,
#V3ReviewWorkspace QLineEdit:focus, #V3ReviewWorkspace QComboBox:focus {border-color:#ffc27b;}
#V2LibraryScreen[shellV3Library="true"],
#V2LibraryScreen[shellV3Library="true"] #V2LibraryWorkbench,
#V2LibraryScreen[shellV3Library="true"] #V2LibraryFilmPanel,
#V2LibraryScreen[shellV3Library="true"] #V2LibraryInspector {background:#111214;border:0;}
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] #V2LibrarySearch {background:#0d0e10;border-color:#262a2f;color:#e6e8e6;}
#V2LibraryScreen[shellV3Library="true"] #V3LibraryInspectorColumn {background:#111214;border-left:1px solid #262a2f;}
#V2LibraryScreen[shellV3Library="true"] QPushButton:disabled {color:#8d949a;border-color:#262a2f;background:#111214;}
"""


V3_TITANIUM_REVIEW_MATERIAL = r"""
/* Keep the user's original dark shell. Only transport accents and fit change. */
QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget::item { padding: 4px 3px; }
QFrame#V3ReviewWorkspace #V2ReviewClipList QTableWidget::item:selected {
    background: #1a2a20; color: #e6e8e6; border-left: 3px solid #e9aa3f;
}
QFrame#V3ReviewWorkspace #V2ReviewClipList QPushButton,
QFrame#V3ReviewWorkspace #V2ReviewClipList QToolButton { padding: 0px 3px; }
QFrame#V3ReviewWorkspace #V2ReviewClipList #V3LedgerFilters QPushButton {
    font-size: 10px; padding: 3px 2px;
}
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton#InspectorSaveNext,
QFrame#V3ReviewActionMirror QPushButton[primary="true"] {
    color: #062312; background: #39e07a; border: 1px solid #39e07a;
}
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton#InspectorSaveNext:hover,
QFrame#V3ReviewActionMirror QPushButton[primary="true"]:hover { background: #4ae88a; }
QFrame#V3ReviewWorkspace #InspectorActionBar QPushButton#InspectorSaveNext:disabled {
    color: #5f8a70; background: #1a3a28; border-color: #1a3a28;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorActionBar QPushButton[accent="true"] {
    background: #111214; color: #e6e8e6; border-color: #262a2f;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector QComboBox,
QFrame#V3ReviewWorkspace #V2ReviewInspector QLineEdit { min-height: 22px; padding: 1px 6px; }
QFrame#V3ReviewWorkspace #V2ReviewInspector QComboBox QLineEdit {
    min-height: 0px; padding: 0px; border: none; background: transparent;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector #V3SourcePhoto QPushButton,
QFrame#V3ReviewWorkspace #V2ReviewInspector #V3SituationPanel QPushButton {
    min-height: 24px; padding: 1px 7px;
}
"""


V3_LIBRARY_PAGEBOOK = """
#V2LibraryScreen[shellV3Library="true"],
#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryWorkbench,
#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryFilmPanel,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector {
    background: #111214; border: none; border-radius: 0; padding: 0;
}
#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch {
    background: #0d0e10; border: 1px solid #262a2f; border-radius: 3px;
    color: #e6e8e6; font: 14px "Segoe UI"; padding: 8px 12px;
}
#V2LibraryScreen[shellV3Library="true"] QComboBox[libraryFilter="true"],
#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"] {
    background: #0d0e10; border: 1px solid #262a2f; border-radius: 3px;
    font: 12px "Segoe UI"; color: #e6e8e6; padding: 4px 10px;
}
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QLineEdit,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QPlainTextEdit {
    background: transparent; color: #e6e8e6; border: 1px solid transparent;
    border-radius: 2px; font: 14px "Segoe UI"; padding: 1px 0;
    selection-background-color: #163a26;
}
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QLineEdit:hover,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QPlainTextEdit:hover {
    background: #0d0e10; border-color: #262a2f;
}
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QLineEdit:focus,
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QPlainTextEdit:focus {
    background: #0d0e10; border-color: #ffc27b;
}
#V2LibraryScreen[shellV3Library="true"] QWidget#V2LibraryInspector QLineEdit#V2LibraryClipTitle {
    font: 600 23px "Segoe UI"; color: #e6e8e6; padding: 0;
}
#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryFieldLabel"] {
    background: transparent; border: none; color: #8d949a; font: 12px "Segoe UI";
}
#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryDetailValue"] {
    color: #e6e8e6; font: 14px "Segoe UI"; background: transparent; border: none;
}
#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryMeta"] {
    color: #8d949a; font: 12px "Segoe UI"; padding: 0;
}
#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryInspectorHeading"] {
    color: #8d949a; font: 600 11px "Segoe UI"; letter-spacing: 1px;
}
#V2LibraryScreen[shellV3Library="true"] QFrame#V3LibraryDetailsRule {
    background: #262a2f; border: none;
}
#V2LibraryScreen[shellV3Library="true"] QFrame#V2LibraryLedgerHeader {
    background: #111214; border: none; border-top: 1px solid #262a2f; border-radius: 0;
}
#V2LibraryScreen[shellV3Library="true"] QLabel[role="ledgerColumn"] {
    color: #8d949a; font: 600 10px "Segoe UI"; letter-spacing: 1px;
}
#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryHint"] {
    color: #8d949a; font: 10px "Segoe UI";
}
#V2LibraryScreen[shellV3Library="true"] QLabel#V3LibraryPreviewTime {
    font: 12px "Consolas"; color: #8d949a; background: transparent; border: none;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryClearFilters,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryClearSelection {
    background: transparent; border: 1px solid transparent; color: #8d949a; font: 12px "Segoe UI";
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryClearFilters:hover,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryClearSelection:hover {
    background: #1c1f23; border-color: #3a4046;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryReviewClip {
    color: #e6e8e6; font: 12px "Segoe UI"; padding: 5px 8px;
}
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QToolButton#V3LibraryBook {
    background: transparent; border: 1px solid transparent; padding: 0;
}
QWidget#TapeSiftV3ApplicationBar[pageMode="library"] QToolButton#V3LibraryBook:hover {
    background: #1c1f23; border-color: #3a4046;
}
#V2LibraryScreen[shellV3Library="true"] QLineEdit#V2LibrarySearch:focus {
    background: #0d0e10; border: 1px solid #ffc27b;
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryClearSelection,
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBuildReel {
    background: #111214; border: 1px solid #262a2f; border-radius: 3px;
    color: #e6e8e6; font: 12px "Segoe UI";
}
#V2LibraryScreen[shellV3Library="true"] QPushButton#V3LibraryBuildReel:disabled {
    background: #111214; border-color: #262a2f; color: #8d949a;
}
"""


V3_REVIEW_PAGEBOOK = """
QWidget#TapeSiftV3ApplicationBar[pageMode="review"] {
    background: #0a0b0c;
    border: none;
    border-bottom: 1px solid #262a2f;
}
QPushButton#V3ReviewLibraryButton, QPushButton#V3ReviewExportButton {
    background: #111214; color: #e6e8e6;
    border: 1px solid #262a2f; border-radius: 3px;
    padding: 4px 10px; font: 12px "Segoe UI";
}
QPushButton#V3ReviewLibraryButton:hover, QPushButton#V3ReviewExportButton:hover { background: #1c1f23; border-color: #3a4046; }
QPushButton#V3ReviewLibraryButton:pressed, QPushButton#V3ReviewExportButton:pressed { background: #163a26; }
QFrame#V3ReviewWorkspace #ClipLedgerHeader,
QFrame#V3ReviewWorkspace #InspectorHeader {
    background: #16181b; border: none;
    border-bottom: 1px solid #262a2f;
    margin: 0px; padding: 0px;
}
QFrame#V3ReviewWorkspace #V2ReviewClipList #ClipLedgerHeader,
QFrame#V3ReviewWorkspace #V2ReviewInspector #InspectorHeader {
    min-height: 58px; max-height: 58px;
    border: none;
}
QFrame#V3ReviewWorkspace #V2ReviewClipList QLabel[role="heading"],
QFrame#V3ReviewWorkspace QLabel#V3DetailsTitle {
    color: #e6e8e6; font-size: 11px; font-weight: 600;
}
QFrame#V3ReviewWorkspace QFrame#V3LedgerProjectCard {
    min-height: 36px; max-height: 36px;
    margin: 0px 10px 3px 10px;
}
QFrame#V3ReviewWorkspace QFrame#V3LedgerCommandCard {
    margin: 0px 10px 0px 10px;
}
QFrame#V3ReviewWorkspace QHeaderView::section {
    background: #111214; color: #8d949a;
    border: none; border-bottom: 1px solid #262a2f;
    padding: 3px 5px; font: 10px "Segoe UI";
}
"""

V3_TAG_MAP_MATERIAL = """
QWidget#V3TagMapTools, QWidget#V3TagMapLegend {
    background: #111214; border: none; border-bottom: 1px solid #262a2f;
}
QWidget#V3TagMapTools QLabel, QWidget#V3TagMapLegend QLabel {
    color: #8d949a; background: transparent; border: none;
    font: 11px "IBM Plex Sans";
}
QWidget#V3TagMapTools QComboBox, QWidget#V3TagMapTools QPushButton {
    min-height: 24px; max-height: 24px; padding: 0px 8px;
    color: #e6e8e6; background: #111214; border: 1px solid #262a2f;
    border-radius: 3px; font: 11px "IBM Plex Sans";
}
QWidget#V3TagMapTools QPushButton:hover, QWidget#V3TagMapTools QComboBox:hover {
    background: #1c1f23; border-color: #3a4046;
}
QWidget#V3TagMapTools QPushButton:pressed { background: #163a26; }
QWidget#V3TagMapTools QPushButton:disabled { color: #8d949a; border-color: #262a2f; }
QWidget#V3TagMapTools QComboBox:focus, QWidget#V3TagMapTools QPushButton:focus {
    border-color: #ffc27b;
}
QWidget#V3TagMapLegend QScrollArea, QWidget#V3TagMapLegend QScrollArea QWidget {
    background: transparent; border: none;
}
QWidget#V3TagMapLegend QScrollBar:horizontal { height: 6px; background: #111214; }
QWidget#V3TagMapLegend QScrollBar::handle:horizontal { background: #3a4046; min-width: 24px; }
QScrollBar#V3TagMapLaneScroll:vertical { width: 10px; background: #111214; margin: 0px; }
QScrollBar#V3TagMapLaneScroll::handle:vertical { background: #3a4046; min-height: 18px; border-radius: 3px; }
QScrollBar#V3TagMapLaneScroll::add-line:vertical,
QScrollBar#V3TagMapLaneScroll::sub-line:vertical { height: 0px; }
QFrame#V3ReviewWorkspace #TagMapHeading { color: #e6e8e6; font-size: 11px; }
QFrame#V3ReviewWorkspace #V2QuickTagTray QPushButton[quickTag="true"] {
    font: 600 11px "IBM Plex Sans"; border-radius: 3px;
}
QFrame#V3ReviewWorkspace #V2QuickTagTray QPushButton[quickTag="true"]:hover {
    border-width: 2px;
}
QFrame#V3ReviewWorkspace #V2QuickTagTray QPushButton[quickTag="true"]:focus {
    border-width: 2px;
}
"""


V3_DETAILS_PAGEBOOK = r"""
#V3SituationPanel, #V3DetailsSection { background: transparent; color: #e6e8e6; }
#V3SituationPanel QWidget { background: transparent; }
#V3SituationPanel QLabel, #V3DetailsSection QLabel {
    background: transparent; border: none; color: #8d949a;
    font: 12px "IBM Plex Sans";
}
#V3SituationPanel QLabel[detailsHeading="true"],
#V3DetailsSection QLabel[detailsHeading="true"] {
    color: #e6e8e6; font: 500 15px "IBM Plex Sans";
}
#V3DetailsSection { border-top: 1px solid #262a2f; }
#V3SituationPanel QLineEdit, #V3SituationPanel QComboBox,
#V3DetailsSection QLineEdit, #V3DetailsSection QComboBox,
#V3GameTeamsDialog QComboBox {
    background: #0d0e10; color: #e6e8e6; border: 1px solid #262a2f;
    border-radius: 3px; min-height: 28px; padding: 2px 6px;
    font: 12px "IBM Plex Sans"; selection-background-color: #163a26;
}
#V3SituationPanel QComboBox QLineEdit,
#V3DetailsSection QComboBox QLineEdit, #V3GameTeamsDialog QComboBox QLineEdit {
    border: none; padding: 0; min-height: 24px;
}
#V3SituationPanel QLineEdit:focus, #V3SituationPanel QComboBox:focus,
#V3DetailsSection QLineEdit:focus, #V3DetailsSection QComboBox:focus {
    border-color: #ffc27b;
}
#V3SituationPanel QPushButton, #V3DetailsSection QPushButton,
#V3DetailsSection QToolButton, #V3GameTeamsDialog QPushButton {
    background: #111214; color: #e6e8e6; border: 1px solid #262a2f;
    border-radius: 3px; min-height: 29px; padding: 2px 7px;
    font: 500 11px "IBM Plex Sans";
}
#V3SituationPanel QPushButton:hover, #V3DetailsSection QPushButton:hover,
#V3DetailsSection QToolButton:hover, #V3GameTeamsDialog QPushButton:hover {
    background: #1c1f23; border-color: #3a4046;
}
#V3DetailsSection QPushButton:checked { background: #163a26; color: #c9f2d7; border-color: #1d7a45; }
#V3DetailsSection QPushButton:pressed { background: #163a26; }
#V3TeamScore { background: #111214; border: 1px solid #262a2f; border-radius: 3px; }
#V3TeamScore QLabel#V3TeamAbbreviation { color: #e6e8e6; font-weight: 600; }
#V3MiniField { background: #111214; border: 1px solid #262a2f; border-radius: 3px; }
#V3SituationHint { color: #8d949a; font-size: 11px; }
#V3GameTeamsDialog { background: #111214; color: #e6e8e6; }
#V3GameTeamsDialog QLabel { color: #e6e8e6; font: 13px "IBM Plex Sans"; }
QFrame#V3ReviewWorkspace #InspectorPlayers, QFrame#V3ReviewWorkspace #InspectorNotes {
    background: transparent; border: none; border-top: 1px solid #262a2f;
    margin-top: 12px; padding-top: 14px; font: 500 15px "IBM Plex Sans";
}
QFrame#V3ReviewWorkspace #InspectorPlayers QComboBox {
    font: 12px "IBM Plex Sans"; min-height: 28px;
}
"""

V3_DETAILS_PAGEBOOK += r"""
QFrame#V3ReviewWorkspace #V2ReviewInspector #V3DetailsSection QPushButton[inspectorTag="true"] {
    background: #111214; color: #e6e8e6; border: 1px solid #262a2f;
    border-radius: 3px; min-height: 29px; padding: 2px 7px;
    font: 500 11px "IBM Plex Sans";
}
QFrame#V3ReviewWorkspace #V2ReviewInspector #V3DetailsSection QPushButton[inspectorTag="true"]:checked {
    background: #163a26; color: #c9f2d7; border-color: #1d7a45;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector #V3DetailsSection QPushButton[inspectorTag="true"]:hover {
    border-color: #3a4046;
}
QFrame#V3ReviewWorkspace #V2ReviewInspector #V3DetailsSection QPushButton[inspectorTag="true"]:pressed {
    background: #163a26;
}
"""


V3_DETAILS_PAGEBOOK += r"""
#V3SourcePhoto { background: transparent; border: none; border-bottom: 1px solid #262a2f; }
#V3SourcePhoto QFrame, #V3SourcePhoto QLabel { background: transparent; border: none; }
#V3SourcePhoto QLabel { color: #8d949a; font: 11px "IBM Plex Sans"; }
#V3SourcePhoto QLabel#V3SourcePhotoPreview { background: #0a0b0c; border: 1px solid #262a2f; }
#V3SourcePhoto QPushButton, #V3SourcePhoto QToolButton,
#V3SourcePhotoViewer QPushButton {
    color: #e6e8e6; background: #111214; border: 1px solid #262a2f;
    border-radius: 3px; min-height: 27px; padding: 2px 5px;
    font: 11px "IBM Plex Sans";
}
#V3SourcePhoto QToolButton:checked { background: transparent; border-color: transparent; }
#V3SourcePhoto QPushButton:hover, #V3SourcePhoto QToolButton:hover,
#V3SourcePhotoViewer QPushButton:hover { border-color: #3a4046; background: #1c1f23; }
#V3SourcePhoto QPushButton:pressed, #V3SourcePhoto QToolButton:pressed { background: #163a26; }
#V3SourcePhoto QPushButton:focus, #V3SourcePhoto QToolButton:focus,
#V3SourcePhotoViewer QPushButton:focus { border-color: #ffc27b; }
#V3SourcePhoto QPushButton:disabled, #V3SourcePhoto QToolButton:disabled { color: #8d949a; border-color: #262a2f; }
#V3SourcePhoto QLabel#V3SourcePhotoMessage { color: #8d949a; }
#V3SourcePhotoViewer, #V3SourcePhotoViewer QScrollArea,
#V3SourcePhotoViewer QScrollArea QWidget { background: #0a0b0c; }
#V3SourcePhotoViewer QLabel { color: #e6e8e6; font: 12px "IBM Plex Sans"; border: none; }
"""


V3_DETECT_PAGEBOOK = r"""
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] {
    background: #080c0d; color: #dce4df; font-family: "Segoe UI"; font-size: 14px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel#DetectDialogTitle {
    font: 600 28px "Segoe UI"; color: #eef1ed;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel#DetectBetaBadge {
    color: #bcc5bf; border: 1px solid #38403c; border-radius: 3px; padding: 4px 10px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel[detectStep="true"] {
    font-size: 15px; color: #9ba59f; border: none; border-bottom: 1px solid #27312d;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel[detectStep="true"][active="true"] {
    color: #65df97; border-bottom: 2px solid #65df97;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QScrollArea#V3DetectStageScroll,
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QScrollArea#V3DetectStageScroll > QWidget,
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QScrollArea#V3DetectStageScroll > QWidget > QWidget {
    background: #080c0d; border: none;
}
QFrame#V3DetectSource { border: 1px solid #2b3530; border-radius: 3px; background: #080c0d; }
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QFrame#DetectSetupGuidance {
    border-left: 1px solid #36423b;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel#All22DetectionNotice {
    color: #bac3bd; border: none; padding: 8px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QPushButton {
    min-height: 30px; padding: 4px 18px; font-family: "Segoe UI"; font-size: 14px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QPushButton:focus {
    border: 1px solid #81d7a2;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel#DetectResultSummary {
    color: #edf0ed; font-size: 20px; font-weight: 600;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QFrame#DetectReviewDetail {
    border: 1px solid #2c3730; border-radius: 3px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel[boundarySample="true"] {
    border: 1px solid #dcb34d;
}
QLabel#V3DetectSampleTime { color: #aeb9b0; font-family: "Consolas"; font-size: 11px; }
QLabel#V3DetectReviewNote { color: #a5b0a8; font-size: 12px; }
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel[setupStep="true"] {
    border-bottom: none; font: 18px "Segoe UI"; min-height: 70px; max-height: 70px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel#V3DetectStepNumber {
    color: #a9b3ad; border: 1px solid #637168; border-radius: 18px; font-size: 17px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel#V3DetectStepNumber[active="true"] {
    color: #65df97; border-color: #65df97;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QFrame#V3DetectStepConnector {
    background: #36423b; border: none;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel[detectStep="true"][setupStep="true"][active="true"] {
    border: none;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] #DetectSetupPage QLabel {
    font: 18px "Segoe UI";
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] #DetectSetupPage QLabel[role="eyebrow"] {
    font-size: 14px; color: #65df97;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] #DetectSetupPage QDoubleSpinBox {
    font: 18px "Segoe UI"; min-height: 40px; max-height: 40px; padding: 6px 12px;
}
QDialog#V2DetectPlaysDialog[shellV3Dialog="true"] QLabel#DetectSourceName,
QFrame#V3DetectSource QLabel { font: 18px "Segoe UI"; }
"""


V3_HEATMAP_PAGEBOOK = r"""
QFrame#V3HeatmapPage { background: #0a0f11; border: none; }
#V3HeatmapPage QWidget { background: transparent; font: 14px "Segoe UI"; color: #dce3dd; }
#V3HeatmapPage QLabel { background: transparent; border: none; padding: 0; }
QFrame#V3HeatmapHeader { background: #090e10; border-bottom: 1px solid #28332e; }
QFrame#V3HeatmapSidebar { background: #0d1315; border-left: 1px solid #28332e; }
QWidget#V3HeatmapSettings { background: #0d1315; }
#V3HeatmapPage QScrollArea { background: transparent; border: none; }
#V3HeatmapPage QPushButton, #V3HeatmapPage QComboBox, #V3HeatmapPage QLineEdit {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #111818,stop:1 #090e10);
    color: #dce3dd; border: 1px solid #39433d; border-radius: 3px;
    padding: 7px 9px; min-height: 22px;
}
#V3HeatmapPage QPushButton:hover, #V3HeatmapPage QComboBox:hover {
    border-color: #75877a; background: #18221d;
}
#V3HeatmapPage QPushButton:pressed { background: #102719; border-color: #64bd88; }
#V3HeatmapPage QPushButton:focus, #V3HeatmapPage QComboBox:focus,
#V3HeatmapPage QLineEdit:focus { border-color: #85caa0; }
#V3HeatmapPage QPushButton:disabled { color: #6b796f; border-color: #27332b; }
#V3HeatmapPage QPushButton[heatmapFormat="true"] { min-height: 34px; padding: 4px; font-size: 13px; }
#V3HeatmapPage QPushButton[heatmapFormat="true"]:checked {
    border-color: #72c68d; background: #111d16;
}
#V3HeatmapPage QPushButton#V3HeatmapSelectAll {
    border: none; background: transparent; color: #68bd88; padding: 0; font-size: 12px;
}
#V3HeatmapPage QCheckBox { min-height: 24px; spacing: 12px; }
#V3HeatmapPage QCheckBox::indicator {
    width: 17px; height: 17px; border: 1px solid #59685e; border-radius: 2px; background: #101914;
}
#V3HeatmapPage QCheckBox::indicator:checked { background: #30c678; border-color: #62cf91; image: url("__HEATMAP_CHECK__"); }
#V3HeatmapPage QCheckBox:focus { outline: 1px solid #9ccaac; }
#V3HeatmapPage QLabel#V3HeatmapTitle { font-size: 20px; font-weight: 600; }
#V3HeatmapPage QLabel#V3HeatmapExportHeading { font-size: 18px; font-weight: 600; }
#V3HeatmapPage QLabel#V3HeatmapSummary { color: #a2aca5; font-size: 13px; }
#V3HeatmapPage QLabel#V3HeatmapNote { color: #96a29a; font-size: 12px; }
#V3HeatmapPage QLabel#V3HeatmapStatus { color: #d5ddb5; font-size: 13px; }
#V3HeatmapPage QFrame#V3HeatmapRule { background: #2a3430; border: none; }
#V3HeatmapPage QPushButton#V3HeatmapExport {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #32cc79,stop:1 #23b966);
    color: #06130b; font-size: 15px; font-weight: 600; border: 1px solid #43d181;
    min-height: 34px; max-height: 34px; padding: 7px 9px;
}
#V3HeatmapPage QPushButton#V3HeatmapExport:hover { background: #4cdb8c; }
#V3HeatmapPage QPushButton#V3HeatmapExport:pressed { background: #20a85c; }
#V3HeatmapPage QPushButton#V3HeatmapExport:disabled { background: #253c2e; color: #789381; border-color: #345640; }
#V3HeatmapPage QFrame#V3HeatmapFooter { background: #080d0f; border-top: 1px solid #29342e; }
#V3HeatmapFooter QLabel { font-size: 12px; color: #9aaa9e; }
QWidget#TapeSiftV3ApplicationBar[pageMode="export"] { background: #080d0f; border-bottom: 1px solid #29342e; }
QWidget#V3ExportMasthead, QWidget#V3ExportMasthead QLabel { background: transparent; border: none; }
QLabel#V3ExportModeLabel { font: 11px "Segoe UI"; color: #a2afa5; }
#V3HeatmapPage QScrollBar:vertical { background: #0a0f11; width: 10px; margin: 0; border: none; }
#V3HeatmapPage QScrollBar:horizontal { background: #0a0f11; height: 10px; margin: 0; border: none; }
#V3HeatmapPage QScrollBar::handle { background: #3e5146; border-radius: 3px; min-height: 32px; min-width: 32px; }
#V3HeatmapPage QScrollBar::handle:hover { background: #5b7564; }
#V3HeatmapPage QScrollBar::add-line, #V3HeatmapPage QScrollBar::sub-line { height: 0; width: 0; border: none; }
#V3HeatmapPage QScrollBar::add-page, #V3HeatmapPage QScrollBar::sub-page { background: transparent; }
""".replace("__HEATMAP_CHECK__", icon_path("checkmark-16.svg").as_posix())


V3_MEDIA_EXPORT_PAGEBOOK = r"""
#V3ExportPage, QFrame#V3ExportPage QWidget { background: #080d0f; }
QFrame#V3ExportPage #V3ExportCard, QFrame#V3ExportPage #V3ExportQueueCard {
    border: none; border-top: 1px solid #28332d; border-radius: 0;
}
#V3ExportBodyScroll, #V3ExportBodyScroll > QWidget { border: none; background: #080d0f; }
QFrame#V3ExportPage QLabel { font: 12px "Segoe UI"; background: transparent; border: none; }
QFrame#V3ExportPage QLabel#V3ExportTitle { font: 600 16px "Segoe UI"; }
QFrame#V3ExportPage QLabel#V3ExportCaption { font-size: 10px; color: #91a196; }
QFrame#V3ExportPage QLabel#V3ExportCardTitle { font: 600 13px "Segoe UI"; }
QFrame#V3ExportPage QLabel#V3ExportStageCell { min-height: 25px; max-height: 25px; font-size: 11px; }
QFrame#V3ExportPage QLabel#V3ExportFilename, QFrame#V3ExportPage QLabel#V3ExportSourceRange {
    color: #bfd0c4; font: 11px "Consolas";
}
QFrame#V3ExportPage QLabel#V3ExportDestination { font: 11px "Consolas"; }
QFrame#V3ExportPage QLabel#V3PackagePlayCopy { font: 12px "Segoe UI"; }
QFrame#V3ExportPage QLabel#V3ExportQuiet, QFrame#V3ExportPage QLabel#V3ExportReady { font-size: 11px; }
QFrame#V3ExportPage QComboBox { font: 12px "Segoe UI"; min-height: 30px; max-height: 30px; }
QFrame#V3ExportPage QPushButton#V3ExportBrowse, QFrame#V3ExportPage QPushButton#V3ExportQueueButton,
QFrame#V3ExportPage QPushButton#V3ExportNumberClips {
    color: #c9d5cd; background: #101719; border: 1px solid #34463b;
    border-radius: 3px; min-height: 30px; padding: 0 9px;
}
QFrame#V3ExportPage QPushButton:hover { border-color: #719982; }
QFrame#V3ExportPage QPushButton:pressed { background: #183b29; }
QFrame#V3ExportPage QPushButton:focus, QFrame#V3ExportPage QFrame#V3PackagePlayRow:focus { border: 1px solid #77c394; }
QFrame#V3ExportPage QFrame#V3PackagePlayRow { border: none; border-bottom: 1px solid #202c26; }
QFrame#V3ExportPage QScrollBar:vertical { background: #080d0f; width: 9px; margin: 0; border: none; }
QFrame#V3ExportPage QScrollBar::handle:vertical { background: #3e5146; min-height: 24px; border-radius: 3px; }
QFrame#V3ExportPage QScrollBar::add-line, QFrame#V3ExportPage QScrollBar::sub-line { height: 0; border: none; }
QFrame#V3ExportPage QScrollBar::add-page, QFrame#V3ExportPage QScrollBar::sub-page { background: transparent; }
"""

V3_MEDIA_EXPORT_PAGEBOOK += r"""
QFrame#V3ExportPage QComboBox { background: #0d1315; border: 1px solid #34413a; border-radius: 3px; padding: 0 6px; }
QFrame#V3ExportPage QFrame#V3ExportField { background: #0d1315; border: 1px solid #29372f; border-radius: 3px; }
QFrame#V3ExportPage QFrame#V3ExportQueueLane { background: #0d1315; border: 1px solid #29372f; border-radius: 3px; }
QFrame#V3ExportPage QLabel#V3ExportDestination { background: transparent; border: none; padding: 0; }
QFrame#V3ExportPage QLabel#V3ExportFieldLabel { color: #a7b5ac; font-size: 11px; }
QFrame#V3ExportPage QLabel#V3ExportSummaryHeading { border-top: 1px solid #29372f; padding-top: 9px; font-weight: 600; }
QFrame#V3ExportPage QLabel#V3PackagePlayCopy { font: 10px "Segoe UI"; }
QFrame#V3ExportPage QLabel#V3ExportQueueBadge { color: #92a397; font-size: 9px; }
QFrame#V3ExportPage QCheckBox::indicator:checked { image: url(__EXPORT_CHECK__); background: #228e56; border: 1px solid #67c88b; }
""".replace("__EXPORT_CHECK__", icon_path("checkmark-16.svg").as_posix())


# The five support dialogs share the standard flat surfaces; other shells retain their theme.
V3_SUPPORT_PAGEBOOK = "\n".join(r"""
__SCOPE__ { background: #0b1011; color: #dce7df; }
__SCOPE__ QWidget { background: transparent; color: #dce7df; font: 14px "Segoe UI"; }
__SCOPE__ QScrollArea, __SCOPE__ QScrollArea > QWidget, __SCOPE__ QScrollArea > QWidget > QWidget { background: #0b1011; border: none; }
__SCOPE__ QListWidget { background: #0a0f10; border: 1px solid #26332d; outline: none; }
__SCOPE__ QListWidget#V3SettingsNav, __SCOPE__ QListWidget#V3SupportNav, __SCOPE__ QListWidget#V3HelpNav { border: none; border-right: 1px solid #29342f; padding: 14px 8px; }
__SCOPE__ QListWidget::item { padding: 10px 8px; min-height: 26px; border: none; border-left: 3px solid transparent; }
__SCOPE__ QListWidget#V3SupportNav[compact="true"]::item { padding: 5px 8px; min-height: 22px; }
__SCOPE__ QListWidget::item:selected { color: #edf8f1; background: #18231e; border-left: 3px solid #51d48b; }
__SCOPE__ QListWidget::item:hover { background: #141f1a; }
__SCOPE__ QListWidget::item:disabled { color: #66746c; }
__SCOPE__ QLabel { background: transparent; border: none; }
__SCOPE__ QWidget[settingsPage="true"] QFrame { background: transparent; border: none; }
__SCOPE__ QLabel[supportRole="title"] { font: 600 23px "Segoe UI"; color: #edf4ef; }
__SCOPE__ QLabel[supportRole="description"], __SCOPE__ QLabel[role="subtle"] { color: #9eaea4; font-size: 13px; }
__SCOPE__ QLabel[supportRole="section"], __SCOPE__ QLabel[role="sectionTitle"] { color: #64d99b; font-weight: 600; }
__SCOPE__ QLabel[supportRole="chip"] { padding: 8px 10px; background: #101717; border: 1px solid #2e3a33; border-radius: 2px; }
__SCOPE__ QFrame[dialogSection="true"] { background: transparent; border-radius: 0; border: none; border-top: 1px solid #29342f; }
__SCOPE__ QFrame#V3SupportRule { background: #29342f; border: none; max-height: 1px; }
__SCOPE__ QWidget#V3SupportFooter { background: #0a0f10; border: none; border-top: 1px solid #29342f; }
__SCOPE__ QLineEdit, __SCOPE__ QComboBox, __SCOPE__ QSpinBox, __SCOPE__ QDoubleSpinBox { background: #0c1213; color: #e2ece6; border: 1px solid #34413a; border-radius: 3px; min-height: 30px; padding: 0 8px; selection-background-color: #245b3c; }
__SCOPE__ QLineEdit:focus, __SCOPE__ QComboBox:focus, __SCOPE__ QSpinBox:focus, __SCOPE__ QDoubleSpinBox:focus { border-color: #65ca92; }
__SCOPE__ QPushButton { color: #dce7df; background: #121a18; border: 1px solid #39473f; border-radius: 3px; min-height: 30px; padding: 0 10px; }
__SCOPE__ QPushButton:hover { background: #1a2821; border-color: #617a6a; }
__SCOPE__ QPushButton:pressed { background: #0b130f; }
__SCOPE__ QPushButton:focus { border-color: #67d096; }
__SCOPE__ QPushButton#V3ResultRemove { min-height: 22px; max-height: 22px; min-width: 22px; padding: 0; border: none; background: transparent; }
__SCOPE__ QPushButton#V3TagSwatch { min-height: 18px; max-height: 18px; min-width: 18px; max-width: 18px; padding: 0; margin: 6px 22px; border-radius: 3px; }
__SCOPE__ QListWidget#ResultFavoritesList::item { padding: 0; min-height: 38px; border: none; border-bottom: 1px solid #29342f; }
__SCOPE__ QLabel[supportRole="stepTitle"] { font: 600 16px "Segoe UI"; }
__SCOPE__ QLabel[supportRole="stepNumber"] { border: 1px solid #63ad80; border-radius: 14px; color: #72d597; }
__SCOPE__ QLabel[supportRole="keycap"] { border: 1px solid #4a5b50; border-radius: 3px; background: #101717; font-weight: 600; }
__SCOPE__ QFrame#V3HelpStep { border: none; border-bottom: 1px solid #253029; border-radius: 0; }
__SCOPE__ QScrollBar:vertical { background: #0b1011; width: 9px; margin: 0; border: none; }
__SCOPE__ QScrollBar:horizontal { background: #0b1011; height: 9px; margin: 0; border: none; }
__SCOPE__ QScrollBar::handle { background: #3e5146; min-height: 26px; min-width: 26px; border-radius: 3px; }
__SCOPE__ QScrollBar::add-line, __SCOPE__ QScrollBar::sub-line { height: 0; width: 0; border: none; }
__SCOPE__ QScrollBar::add-page, __SCOPE__ QScrollBar::sub-page { background: transparent; }
__SCOPE__ QPushButton:disabled { color: #6d7c73; border-color: #29372f; }
__SCOPE__ QPushButton[primary="true"], __SCOPE__ QPushButton#V3SettingsSave { background: #32d77b; color: #05150c; border: 1px solid #75e3a3; font-weight: 600; min-height: 36px; }
__SCOPE__ QPushButton[primary="true"]:hover, __SCOPE__ QPushButton#V3SettingsSave:hover { background: #52e18f; }
__SCOPE__ QCheckBox { background: transparent; spacing: 8px; min-height: 26px; }
__SCOPE__ QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #687e6f; border-radius: 2px; background: #0d1510; }
__SCOPE__ QCheckBox::indicator:checked { background: #218f56; border-color: #61d596; image: url(__CHECK__); }
__SCOPE__ QTableWidget, __SCOPE__ QTreeWidget { background: #0c1213; color: #dce7df; border: 1px solid #2b3931; gridline-color: #2a352f; selection-background-color: #213c2c; outline: none; }
__SCOPE__ QTreeWidget::item { padding: 7px 4px; border-bottom: 1px solid #233029; }
__SCOPE__ QHeaderView::section { background: #0b1011; color: #9eaea4; border: none; padding: 7px; }
__SCOPE__ QTextBrowser { background: #0b1011; color: #dce7df; border: none; padding: 22px; }
""".replace("__SCOPE__", f'QDialog#{name}[supportDialog="true"]').replace("__CHECK__", icon_path("checkmark-16.svg").as_posix())
    for name in ("V3SettingsDialog", "V3ProjectSettingsDialog", "V3TagColorsDialog", "V3ResultManagerDialog", "V3HowItWorksDialog"))


V3_BROADCAST_SLATE = r"""
/* Dark broadcast slate: the containing surface paints the retained grain once. */
QWidget#TapeSiftV3ApplicationBar[pageMode="home"], QWidget#TapeSiftV3ApplicationBar[pageMode="library"] {background:transparent;border:0;}
QWidget#TapeSiftV3ApplicationBar QWidget, QWidget#TapeSiftV3ApplicationBar QMenuBar {background:transparent;border:0;}
QWidget#TapeSiftV3ApplicationBar QLabel, QWidget#TapeSiftV3ApplicationBar QPushButton, QWidget#TapeSiftV3ApplicationBar QToolButton {background:transparent;}
QWidget#TapeSiftV3ApplicationBar #HomeSectionLabel, QWidget#TapeSiftV3ApplicationBar #LibrarySectionLabel {color:#39e07a;font:600 15px 'Segoe UI';border-bottom:2px solid #39e07a;padding:8px;}
QWidget#TapeSiftV3ApplicationBar #HomeBrandDivider, QWidget#TapeSiftV3ApplicationBar #V3LibraryNavDivider {background:#262a2f;}
QWidget#TapeSiftV3ApplicationBar #V3LibraryRebuild {border:1px solid #262a2f;border-top-color:#262a2f;border-radius:3px;padding:0 8px;}
QWidget#V2StartScreen[shellV3Home="true"], QWidget#V2StartScreen[shellV3Home="true"] QWidget, QWidget#V2StartScreen[shellV3Home="true"] QScrollArea, QWidget#V2StartScreen[shellV3Home="true"] QLabel {background:transparent;}
QWidget#V2StartScreen[shellV3Home="true"] QLabel {color:#e6e8e6;}
QWidget#V2StartScreen[shellV3Home="true"] QFrame#V3ResumeCard {background:#13161b;border:1px solid #303640;border-radius:8px;}
QWidget#V2StartScreen[shellV3Home="true"] QFrame#V3ResumeCard[featured="true"] {background:#171b21;border-color:#3b444e;}
QWidget#V2StartScreen[shellV3Home="true"] QWidget#V3HomeFeature {border:0;}
QWidget#V2StartScreen[shellV3Home="true"] QProgressBar {background:#262a2f;border:0;border-radius:3px;}
QWidget#V2StartScreen[shellV3Home="true"] QProgressBar::chunk {background:#1d7a45;border-radius:3px;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton {background:#111214;color:#e6e8e6;border:1px solid #262a2f;border-top-color:#262a2f;border-radius:3px;padding:4px 12px;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton:hover {background:#1c1f23;border-color:#3a4046;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[homePrimary="true"] {background:#39e07a;color:#062312;border-color:#39e07a;border-top-color:#39e07a;font-weight:600;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[homePrimary="true"]:hover {background:#4ae88a;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton:disabled {background:#111214;color:#8d949a;border-color:#262a2f;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[homePrimary="true"]:disabled {background:#1a3a28;color:#5f8a70;border-color:#1a3a28;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[homeStartAction="true"] {font:600 17px 'Segoe UI';min-height:32px;max-height:32px;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction {min-width:0;max-width:16777215;min-height:28px;font-size:16px;}
QWidget#V2StartScreen[shellV3Home="true"] QScrollBar:vertical, #V2LibraryScreen QScrollBar:vertical {background:#0d0e10;width:9px;margin:0;border:0;}
QWidget#V2StartScreen[shellV3Home="true"] QScrollBar::handle:vertical, #V2LibraryScreen QScrollBar::handle:vertical {background:#3a4046;min-height:30px;border-radius:3px;}
QWidget#V2StartScreen[shellV3Home="true"] QScrollBar::add-line, QWidget#V2StartScreen[shellV3Home="true"] QScrollBar::sub-line {height:0;}
QWidget#V2LibraryScreen[shellV3Library="true"], QWidget#V2LibraryScreen[shellV3Library="true"] QWidget,
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibraryWorkbench,
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibraryFilmPanel,
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibraryInspector,
QWidget#V2LibraryScreen[shellV3Library="true"] #V3LibraryInspectorColumn {background:transparent;border:0;}
QWidget#V2LibraryScreen[shellV3Library="true"] QLabel {background:transparent;color:#e6e8e6;}
QWidget#V2LibraryScreen[shellV3Library="true"] QLineEdit,
QWidget#V2LibraryScreen[shellV3Library="true"] QComboBox,
QWidget#V2LibraryScreen[shellV3Library="true"] QTextEdit,
QWidget#V2LibraryScreen[shellV3Library="true"] QSpinBox,
QWidget#V2LibraryScreen[shellV3Library="true"] QToolButton[libraryFilter="true"],
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibrarySearch {
 background:#0d0e10;color:#e6e8e6;border:1px solid #262a2f;border-top-color:#262a2f;border-radius:3px;min-height:30px;padding:0 9px;selection-background-color:#163a26;
}
QWidget#V2LibraryScreen[shellV3Library="true"] QLineEdit:focus,
QWidget#V2LibraryScreen[shellV3Library="true"] QComboBox:focus,
QWidget#V2LibraryScreen[shellV3Library="true"] QTextEdit:focus,
QWidget#V2LibraryScreen[shellV3Library="true"] QSpinBox:focus {border-color:#ffc27b;}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton {
 background:#111214;color:#e6e8e6;border:1px solid #262a2f;border-top-color:#262a2f;border-radius:3px;padding:0 10px;min-height:32px;
}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton:hover {background:#1c1f23;border-color:#3a4046;}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton[primary="true"] {
 background:#39e07a;color:#062312;border-color:#39e07a;border-top-color:#39e07a;font-weight:600;
}
QWidget#V2LibraryScreen[shellV3Library="true"] #V3LibraryReviewClip {background:#111214;color:#e6e8e6;border-color:#1d7a45;font-weight:600;}
QWidget#V2LibraryScreen[shellV3Library="true"] #V3LibraryReviewClip:hover {border-color:#39e07a;}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton[primary="true"]:hover {background:#4ae88a;}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton:disabled {background:#111214;color:#8d949a;border-color:#262a2f;}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton[primary="true"]:disabled {background:#1a3a28;color:#5f8a70;border-color:#1a3a28;}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton:focus,
QWidget#V2LibraryScreen[shellV3Library="true"] #V3LibraryReviewClip:focus,
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton[primary="true"]:focus,
QWidget#V2LibraryScreen[shellV3Library="true"] QToolButton:focus {border-color:#ffc27b;}
QWidget#V2LibraryScreen[shellV3Library="true"] QPushButton[primary="true"]:focus {background:#4ae88a;}
QWidget#V2LibraryScreen[shellV3Library="true"] QListWidget {background:#111214;alternate-background-color:#111214;border:0;}

QWidget#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects {background:transparent;border:0;border-radius:0;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction {min-width:0;max-width:16777215;min-height:28px;max-height:40px;font-size:16px;color:#e6e8e6;border:1px solid #262a2f;border-top-color:#262a2f;background:#111214;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction[homePrimary="true"] {background:#39e07a;color:#062312;border-color:#39e07a;border-top-color:#39e07a;font-weight:600;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction[homePrimary="true"]:hover {background:#4ae88a;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton:focus,
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction:focus {border-color:#ffc27b;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[homePrimary="true"]:focus,
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction[homePrimary="true"]:focus {background:#4ae88a;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[libraryEntry="true"] {min-width:80px;max-width:80px;border:0;border-bottom:0;background:transparent;color:#e6e8e6;font:15px 'Segoe UI';padding:0;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton#ApplicationSettingsButton {min-width:85px;max-width:85px;border:0;background:transparent;color:#e6e8e6;font:15px 'Segoe UI';padding:0;}
QWidget#V2LibraryScreen[shellV3Library="true"] #V3LibraryMainSurface,
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibraryPreview,
QWidget#V2LibraryScreen[shellV3Library="true"] #V3LibrarySelectedSummary,
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibraryInspector {background:transparent;border:0;}
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibraryWorkbench {background:#15181d;border:1px solid #303640;border-radius:7px;}
QWidget#V2LibraryScreen[shellV3Library="true"] #V3LibraryLedger {background:#101317;border:1px solid #262e37;border-radius:7px;}
QWidget#V2LibraryScreen[shellV3Library="true"] #V2LibraryLedgerHeader {background:#1b2027;border:0;}
QWidget#V2LibraryScreen[shellV3Library="true"] QLabel[role="librarySection"] {font:600 15px 'IBM Plex Sans';color:#e6e8e6;padding:10px 0;}
QWidget#V2LibraryScreen[shellV3Library="true"] QLabel[role="ledgerColumn"] {font:600 11px 'IBM Plex Sans';color:#a7b2be;letter-spacing:0.7px;}
QWidget#V2LibraryScreen[shellV3Library="true"] QLabel[role="libraryFieldLabel"] {font:500 12px 'IBM Plex Sans';color:#a7b2be;}

QWidget#V2StartScreen[shellV3Home="true"] QPushButton#ProjectPrimaryAction[homePrimary="true"] {min-width:208px;max-width:208px;min-height:38px;max-height:38px;padding:0;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton#V3HomeLibraryLink {border:0;border-bottom:0;background:transparent;color:#e6e8e6;min-width:80px;max-width:80px;font:15px 'Segoe UI';padding:0;}
QWidget#TapeSiftV3ApplicationBar QMenuBar::extension {image:url(__MENU_MORE__);width:28px;padding:0;border:1px solid #262a2f;background:#111214;}
""".replace("__MENU_MORE__", (icon_path("more-horizontal-16.svg").parents[2] / "branding/more-menus.png").as_posix())


# The Film Room lock is scoped to Home; Review and Library retain their palettes.
V3_FILM_ROOM = r"""
QWidget#V2StartScreen[shellV3Home="true"],
QWidget#V2StartScreen[shellV3Home="true"] QScrollArea,
QWidget#V2StartScreen[shellV3Home="true"] QScrollArea > QWidget > QWidget {
    background:#11110f;border:0;
}
QWidget#V2StartScreen[shellV3Home="true"] QWidget#V3HomeReturningProjects,
QWidget#V2StartScreen[shellV3Home="true"] QFrame#V3CinematicProject,
QWidget#V2StartScreen[shellV3Home="true"] QWidget#V3FilmEmptyCopy,
QWidget#V2StartScreen[shellV3Home="true"] QWidget#V3FilmEmptyCopy QLabel {
    background:transparent;border:0;padding:0;margin:0;
}
QWidget#V2StartScreen[shellV3Home="true"] QLabel#V3FilmEmptySubtitle {
    color:#c1c0b9;font:23px 'Segoe UI';background:transparent;
}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[filmAction="true"],
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[filmAction="true"] {
    background:transparent;color:#eeeade;border:1px solid #59574e;border-radius:4px;
    padding:0;min-width:0;max-width:16777215;min-height:0;max-height:16777215;
    font:16px 'Segoe UI';
}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton#V3HomeNewProject {border-color:#998451;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[filmPrimary="true"],
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#V3FilmOpen[filmPrimary="true"] {
    background:#f1eddf;color:#161610;border:1px solid #f1eddf;border-radius:4px;
    padding:0;min-width:0;max-width:16777215;min-height:0;max-height:16777215;font:600 18px 'Segoe UI';
}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[filmPrimary="true"]:hover {background:#fffdf4;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton[filmAction="true"]:hover,
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[filmAction="true"]:hover {border-color:#d3bf83;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#V3FilmOpen,
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#V3FilmOverflow,
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#V3FilmLibrary {
    color:#baa36a;background:transparent;border:1px solid transparent;border-radius:3px;
    min-width:0;max-width:16777215;min-height:0;max-height:16777215;padding:4px;font:16px 'Segoe UI';
}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton:focus {border:1px solid #d3bf83;}
QWidget#V2StartScreen[shellV3Home="true"] QPushButton#V3FilmOpen:disabled {background:#24231e;color:#8b887f;border-color:#454238;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QWidget {background:transparent;border:0;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[filmNav="true"] {
    color:#eeeade;background:transparent;border:0;border-bottom:3px solid transparent;
    min-width:0;max-width:16777215;min-height:0;max-height:16777215;padding:0;font:16px 'Segoe UI';
}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton#V3FilmHomeTab {border-bottom-color:#bba36a;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[filmNav="true"]:hover {color:#d3bf83;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[filmNav="true"]:focus {border:1px solid #d3bf83;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton#V3FilmWordmark {border:0;background:transparent;padding:0;min-width:0;max-width:16777215;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QMenuBar#CenteredApplicationMenuBar {background:transparent;color:#ddd9ce;font:12px 'Segoe UI';padding:0;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QMenuBar#CenteredApplicationMenuBar::item {padding:2px 12px;background:transparent;}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QMenuBar#CenteredApplicationMenuBar::item:selected {background:#29261e;}

QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[filmNav="true"] {
    min-width:112px;max-width:112px;min-height:48px;max-height:48px;border-radius:0;
}
QWidget#TapeSiftV3ApplicationBar[pageMode="home"] QPushButton[filmAction="true"] {
    min-height:48px;max-height:48px;
}
QWidget#V2StartScreen[shellV3Home="true"] QWidget#V3FilmEmptyCopy QPushButton[filmAction="true"] {
    min-width:236px;max-width:236px;min-height:64px;max-height:64px;
}
"""
