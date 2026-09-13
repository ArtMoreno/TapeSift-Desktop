"""The source viewer never labels decoded-but-unpainted pixels as painted."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.source_photo import make_photo, source_identity
from tapesift.tests.test_source_photo import png_bytes
from tapesift.ui_core.video_player import StepVideoWidget
from tapesift.ui_v3.source_photo import SourcePhotoPanel, SourcePhotoViewer, painted_snapshot


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_painted_snapshot_rejects_new_decoded_pixels_and_unsettled_identity(app):
    surface = StepVideoWidget()
    surface.resize(100, 100)
    surface.show()
    app.processEvents()
    state = {"pts": None}
    surface.frame_presented.connect(lambda pts: state.update(pts=pts))
    player = SimpleNamespace(video_widget=surface, displayed_position_ms=lambda: state["pts"],
        _next_presented_frame_is_hard_seek=False, _selection_epoch=1, _last_presented_selection_epoch=1)
    def frame(color, pts):
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        image.fill(color)
        native_frame = QVideoFrame(image)
        native_frame.setStartTime(pts * 1000)
        return native_frame
    surface._frame_arrived(frame(Qt.GlobalColor.red, 1000))
    surface.repaint()
    first, pts = painted_snapshot(player)
    assert pts == 1000 and first.pixelColor(0, 0).red() == 255
    surface._frame_arrived(frame(Qt.GlobalColor.blue, 1016))
    assert painted_snapshot(player) is None
    surface.repaint()
    second, pts = painted_snapshot(player)
    assert pts == 1016 and second.pixelColor(0, 0).blue() == 255
    assert first.pixelColor(0, 0).red() == 255
    player._next_presented_frame_is_hard_seek = True
    assert painted_snapshot(player) is None
    player._next_presented_frame_is_hard_seek = False
    player._selection_epoch += 1
    assert painted_snapshot(player) is None
    player._last_presented_selection_epoch = player._selection_epoch
    surface._frame_arrived(frame(Qt.GlobalColor.green, -1))
    surface.repaint()
    assert painted_snapshot(player) is None
    surface.close()


def test_photo_panel_retains_missing_film_image_but_clears_other_clip_and_corruption(app, tmp_path):
    film = tmp_path / "source.mp4"
    film.write_bytes(b"Unit-test identity")
    clip = Clip(1000, 3000, clip_number=1, details={"custom":"kept"})
    png = png_bytes()
    identity = source_identity(str(film))
    clip.source_photo = make_photo(clip, png, 500, identity, identity)
    clip.source_photo_png = png
    panel = SourcePhotoPanel()
    panel.set_source(str(film))
    panel.set_clip(clip)
    panel.resize(294, 220)
    panel.show()
    app.processEvents()
    assert panel.view_button.isEnabled() and "before clip" in panel.caption.text()
    assert "00:00.500" in panel.caption.text()
    assert panel._image.size() == QImage.fromData(png).size()
    film.unlink()
    panel.refresh()
    assert panel.view_button.isEnabled() and "unavailable" in panel.caption.text()
    viewer = SourcePhotoViewer(panel._image, panel._caption)
    viewer.show()
    app.processEvents()
    viewer.actual_size.click()
    assert viewer.preview.pixmap().size() == panel._image.size()
    viewer.reject()
    panel.heading.click()
    assert panel.body.isHidden()
    panel.heading.click()
    assert not panel.body.isHidden()
    clip.source_photo_png = png[:-4]
    panel.refresh()
    assert not panel.view_button.isEnabled() and "integrity" in panel.caption.text()
    panel.set_clip(Clip(4000, 6000))
    assert panel._image.isNull() and "No source photo" in panel.caption.text()
    panel.set_clip(None)
    assert not panel.capture_button.isEnabled()
    assert clip.details == {"custom":"kept"}
    panel.close()


def test_capture_refuses_file_replaced_since_load_and_cancels_timeout(app, tmp_path):
    from PySide6.QtCore import QTimer
    from types import MethodType
    from tapesift.ui_v3.main_window import MainWindowV3

    film = tmp_path / "loaded.mp4"
    film.write_bytes(b"Original loaded source")
    identity = source_identity(str(film))
    panel = SourcePhotoPanel()
    clip = Clip(1000, 3000)
    clip.source_photo = make_photo(clip, png_bytes(), 500, identity, identity)
    clip.source_photo_png = png_bytes()
    previous = dict(clip.source_photo)
    panel.set_clip(clip)
    context = (object(), clip.id, clip.start_ms, clip.end_ms, str(film), str(film), 1)
    owner = SimpleNamespace(_v3_photo_request=None, _v3_photo_captured_request=None,
        _v3_photo_loaded_identity=(identity, identity), _v3_photo_timer=QTimer(),
        clip_editor=SimpleNamespace(source_photo_panel=panel), _source_photo_context=lambda: context,
        _source_photo_frame_ready=lambda: None)
    for name in ("_capture_source_photo", "_cancel_source_photo", "_source_photo_timeout"):
        setattr(owner, name, MethodType(getattr(MainWindowV3, name), owner))
    owner._v3_photo_timer.setSingleShot(True)
    owner._v3_photo_timer.setInterval(1)
    owner._v3_photo_timer.timeout.connect(owner._source_photo_timeout)
    owner._capture_source_photo()
    assert owner._v3_photo_request is not None and panel._pending
    owner._capture_source_photo()
    assert owner._v3_photo_request is None and not panel._pending
    owner._capture_source_photo()
    from PySide6.QtTest import QTest
    for _ in range(100):
        if owner._v3_photo_request is None:
            break
        QTest.qWait(10)
    assert owner._v3_photo_request is None and "No settled frame" in panel.message.text()
    film.write_bytes(b"Replacement after the decoded frame was loaded")
    owner._capture_source_photo()
    assert owner._v3_photo_request is None and "loaded film changed" in panel.message.text()
    assert clip.source_photo == previous and clip.source_photo_png == png_bytes()
    panel.close()


def test_source_reference_allows_detail_edits_and_tracks_the_selected_clip(app, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QHBoxLayout, QWidget
    from tapesift.ui_v3.clip_details import ClipDetailsV3

    film = tmp_path / "source.mp4"
    film.write_bytes(b"Unit-test source identity")
    identity = source_identity(str(film))
    clips = []
    for number, color in ((1, Qt.GlobalColor.red), (2, Qt.GlobalColor.blue)):
        image = QImage(1280, 720, QImage.Format.Format_RGB32)
        image.fill(color)
        path = tmp_path / f"frame-{number}.png"
        assert image.save(str(path), "PNG")
        png = path.read_bytes()
        clip = Clip(number * 1000, number * 1000 + 500, clip_number=number,
                    details={"custom": "kept"})
        clip.source_photo = make_photo(clip, png, number * 1000, identity, identity)
        clip.source_photo_png = png
        clips.append(clip)
    host = QWidget()
    row = QHBoxLayout(host)
    row.addStretch()
    editor = ClipDetailsV3()
    editor.set_analyst_mode(True)
    editor.setFixedWidth(320)
    row.addWidget(editor)
    host.resize(1248, 900)
    editor.set_clip(clips[0])
    panel = editor.source_photo_panel
    panel.set_source(str(film))
    host.show()
    app.processEvents()
    # Reintroducing exec() must fail the check instead of hanging the suite.
    bailout = QTimer()
    bailout.setSingleShot(True)
    bailout.timeout.connect(lambda: app.activeModalWidget().reject()
                            if app.activeModalWidget() else None)
    bailout.start(300)
    panel.view_button.click()
    bailout.stop()
    app.processEvents()
    viewer = panel._viewer
    assert viewer is not None and viewer.isVisible()
    assert app.activeModalWidget() is None and not viewer.isModal()
    viewer.actual_size.click()
    assert viewer.preview.pixmap().size() == panel._image.size()
    assert viewer.scroll.horizontalScrollBar().maximum() > 0

    host.activateWindow()
    app.processEvents()
    distance = editor.context_panel.distance_edit
    editor.form_area.ensureWidgetVisible(distance)
    QTest.mouseClick(distance, Qt.MouseButton.LeftButton)
    QTest.keyClicks(distance, "8")
    editor.context_panel.down_combo.setCurrentIndex(3)
    assert distance.hasFocus() and viewer.isVisible()
    assert editor._apply()
    assert clips[0].details["down_distance"] == "3rd & 8"
    assert clips[0].details["custom"] == "kept"
    original_photo = clips[0].source_photo_png

    # Repeated opens reuse the reference and keep its size/scale.
    viewer.resize(700, 500)
    viewer.close()
    panel.view_button.click()
    assert panel._viewer is viewer and viewer.width() == 700
    assert viewer.actual_size.isChecked()
    editor.set_clip(clips[1])
    assert viewer.isVisible() and "Clip 02" in viewer.caption.text()
    assert viewer.image.pixelColor(0, 0).blue() == 255
    clips[1].source_photo_png = clips[1].source_photo_png[:-4]
    panel.refresh()
    assert viewer.image.isNull() and viewer.preview.pixmap().isNull()
    assert "integrity" in viewer.caption.text()
    editor.set_clip(Clip(5000, 6000, clip_number=3))
    assert viewer.isVisible() and "Clip 03" in viewer.caption.text()
    assert viewer.image.isNull() and not viewer.actual_size.isEnabled()
    editor.set_clip(clips[0])
    assert viewer.image.pixelColor(0, 0).red() == 255
    assert clips[0].source_photo_png == original_photo
    film.unlink()
    panel.refresh()
    assert not viewer.image.isNull() and "unavailable" in viewer.caption.text()
    editor.set_clip(None)
    assert not viewer.isVisible() and viewer.image.isNull()
    editor.set_clip(clips[0])
    panel.view_button.click()
    assert viewer.isVisible()
    host.hide()
    app.processEvents()
    assert not viewer.isVisible()
    host.close()
