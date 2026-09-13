"""V3 presentation keeps shared review decisions and exact requested sample ownership."""
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QLabel,QStyle,QStyleOptionButton
from PySide6.QtTest import QTest
from tapesift.services.play_detect_service import DetectionResult,DetectedPlay,UnclassifiedSegment
from tapesift.ui_v3.detection_dialog import PlayDetectDialogV3,boundary_sample_times
from tapesift.ui_v3.theme import stylesheet


@pytest.fixture
def dialog():
    app=QApplication.instance() or QApplication([])
    previous=app.styleSheet()
    app.setStyleSheet(stylesheet())
    window=PlayDetectDialogV3('',Path('missing-unit-test-source.mp4'),20000)
    yield window
    window.reject()
    app.setStyleSheet(previous)


def test_setup_is_visible_and_footer_remains_outside_scrolling_body(dialog):
    dialog.show()
    QTest.qWait(50)
    assert all(w.isVisible() for w in (dialog.separator_spin,dialog.min_spin,dialog.max_spin,dialog.scope_notice))
    assert not dialog.stage_scrolls[0].isAncestorOf(dialog.run_btn)
    assert dialog.findChild(QLabel,'DetectDialogTitle').isVisible()
    dialog._set_stage('analyze')
    QTest.qWait(50)
    assert not dialog.findChild(QLabel,'DetectDialogTitle').isVisible()
    assert dialog.analysis_card.width() >= min(780,dialog.width()-180)
    assert dialog._v3_activity_timer.isActive()
    dialog._cancel_or_reject()
    assert dialog._stage=='setup' and not dialog._v3_activity_timer.isActive()


def test_keep_dismiss_and_first_angle_use_existing_candidate_semantics(dialog):
    result=DetectionResult([DetectedPlay(1000,10000,2,[1000,5000])],
        'scene',3,1,20000,unclassified=[UnclassifiedSegment(10000,15000,1,'uncertain')])
    dialog._done(result)
    assert len(dialog.play_candidates())==1 and dialog.table.rowCount()==1
    dialog.keep_button.click()
    assert len(dialog.play_candidates())==2
    dialog.dismiss_button.click()
    assert len(dialog.play_candidates())==1
    dialog.all_filter.click()
    assert dialog.table.rowCount()==2
    dialog.show();QTest.qWait(50)
    assert dialog.review_detail.isVisible() and not dialog.confident_table.isVisible()
    dialog.stage_scrolls[2].ensureWidgetVisible(dialog.wide_only_check);QTest.qWait(30)
    option=QStyleOptionButton();dialog.wide_only_check.initStyleOption(option)
    rect=dialog.wide_only_check.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator,option,dialog.wide_only_check)
    QTest.mouseClick(dialog.wide_only_check,Qt.MouseButton.LeftButton,pos=rect.center())
    candidate=dialog.play_candidates()[0]
    assert candidate['created_end_ms']==5000 and candidate['detector_end_ms']==10000
    QTest.keyClick(dialog.wide_only_check,Qt.Key.Key_Space)
    assert dialog.play_candidates()[0]['created_end_ms']==10000
    assert not dialog.stage_scrolls[2].isAncestorOf(dialog.create_button)


def test_boundary_requests_clamp_without_claiming_or_accepting_stale_pixels(dialog,tmp_path):
    from PySide6.QtGui import QImage
    assert boundary_sample_times(1000,5000,6000)==[0,750,1000,1250,4750,5000]
    assert boundary_sample_times(0,100,100)==[0,0,0,99,0,99]
    image=QImage(16,9,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.red)
    path=tmp_path/'frame.png';assert image.save(str(path))
    dialog._preview_generation=2;dialog._preview_ids={'current':0}
    dialog._preview_ready(1,'current',str(path))
    assert not dialog._v3_preview_pixmaps
    dialog._preview_ready(2,'old',str(path))
    assert not dialog._v3_preview_pixmaps
    dialog._preview_ready(2,'current',str(path))
    assert dialog._v3_preview_pixmaps[0].size()==image.size()
