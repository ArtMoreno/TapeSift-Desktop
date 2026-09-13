"""The shipped inspector keeps First Read visible through layout changes."""
import json

from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.first_read_service import FirstRead, store
from tapesift.ui_v3.clip_details import ClipDetailsV3


def test_first_read_remains_visible_after_v3_reflow():
    app = QApplication.instance() or QApplication([])
    editor = ClipDetailsV3()
    editor.set_analyst_mode(True)
    clip = Clip(0, 5000, analysis=json.loads(store("", FirstRead(
        label="run", agreement=True, views=("run", "run")))))
    editor.set_clip(clip)
    editor.show()
    for width in (390, 540):
        editor.resize(width, 850)
        editor._reflow_details()
        app.processEvents()
        assert editor.first_read_card.isVisible()
        assert not editor.first_read_card.visibleRegion().isEmpty()
        assert editor.first_read_chip.text() == "RUN"
        assert not clip.details.get("run_pass")
    editor.set_clip(Clip(6000, 11000))
    assert editor.first_read_card.isHidden()
