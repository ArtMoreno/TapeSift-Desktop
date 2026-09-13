"""Native desktop reviewer for Iteration 7I center/ball anchors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


VALID_STATUSES = frozenset({"visible", "occluded", "unsure"})


class AnchorLabelStore:
    """Package-backed JSONL state with stale-frame protection."""

    def __init__(self, package_path: Path, labels_path: Path | None = None) -> None:
        self.package_path = package_path.resolve()
        self.labels_path = (
            labels_path.resolve()
            if labels_path is not None
            else self.package_path.with_name("labels.jsonl")
        )
        package = json.loads(self.package_path.read_text(encoding="utf-8"))
        records = package.get("items")
        if not isinstance(records, list) or not records:
            raise ValueError("Anchor package has no review items")
        self.package_id = str(package.get("package_id", ""))
        self.records: list[dict[str, Any]] = [
            dict(record) for record in records if isinstance(record, dict)
        ]
        if len(self.records) != len(records):
            raise ValueError("Anchor package contains an invalid review item")
        self.by_id = {
            str(record["review_item_id"]): record for record in self.records
        }
        if len(self.by_id) != len(self.records):
            raise ValueError("Anchor package contains duplicate review items")
        self.labels: dict[str, dict[str, Any]] = {}
        self._load()

    def _validate(self, label: dict[str, Any]) -> None:
        review_item_id = str(label.get("review_item_id", ""))
        record = self.by_id.get(review_item_id)
        if record is None:
            raise ValueError(f"Unknown anchor review item: {review_item_id!r}")
        if label.get("reference_sha256") != record.get("reference_sha256"):
            raise ValueError(f"Stale reference frame for {review_item_id}")
        status = str(label.get("anchor_status", ""))
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported anchor status: {status!r}")
        x = label.get("anchor_x")
        y = label.get("anchor_y")
        if status == "visible":
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                raise ValueError("Visible anchor requires coordinates")
            if not 0.0 <= float(x) <= 1.0 or not 0.0 <= float(y) <= 1.0:
                raise ValueError("Anchor coordinates must be normalized")
        elif x is not None or y is not None:
            raise ValueError("Unavailable anchor cannot contain coordinates")

    def _load(self) -> None:
        if not self.labels_path.is_file():
            return
        with self.labels_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                label = json.loads(line)
                if not isinstance(label, dict):
                    raise ValueError(
                        f"{self.labels_path}:{line_number} is not an object"
                    )
                self._validate(label)
                self.labels[str(label["review_item_id"])] = label

    def save(self) -> None:
        self.labels_path.parent.mkdir(parents=True, exist_ok=True)
        ordered = [
            self.labels[str(record["review_item_id"])]
            for record in self.records
            if str(record["review_item_id"]) in self.labels
        ]
        text = "".join(
            json.dumps(label, sort_keys=True) + "\n" for label in ordered
        )
        temporary = self.labels_path.with_name(f"{self.labels_path.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.labels_path)

    def mark_visible(self, review_item_id: str, x: float, y: float) -> None:
        record = self.by_id[review_item_id]
        label = {
            "review_item_id": review_item_id,
            "anchor_status": "visible",
            "anchor_x": round(float(x), 6),
            "anchor_y": round(float(y), 6),
            "reference_sha256": record["reference_sha256"],
        }
        self._validate(label)
        self.labels[review_item_id] = label
        self.save()

    def mark_unavailable(self, review_item_id: str, status: str) -> None:
        record = self.by_id[review_item_id]
        label = {
            "review_item_id": review_item_id,
            "anchor_status": status,
            "anchor_x": None,
            "anchor_y": None,
            "reference_sha256": record["reference_sha256"],
        }
        self._validate(label)
        self.labels[review_item_id] = label
        self.save()

    def clear(self, review_item_id: str) -> None:
        self.labels.pop(review_item_id, None)
        self.save()


class AnchorImage(QLabel):
    """Aspect-fit image that reports clicks in source-normalized space."""

    point_clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(720, 390)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setStyleSheet("background:#050806;border:1px solid #33463a")
        self._source = QPixmap()
        self._marker: tuple[float, float] | None = None

    def set_image(
        self, path: Path, marker: tuple[float, float] | None = None
    ) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise FileNotFoundError(path)
        self._source = pixmap
        self._marker = marker
        self._rescale()

    def set_marker(self, marker: tuple[float, float] | None) -> None:
        self._marker = marker
        self.update()

    def _rescale(self) -> None:
        if self._source.isNull():
            return
        self.setPixmap(self._source.scaled(
            self.contentsRect().size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        self.update()

    def _image_rect(self) -> QRectF:
        shown = self.pixmap()
        if shown.isNull():
            return QRectF()
        left = (self.width() - shown.width()) / 2
        top = (self.height() - shown.height()) / 2
        return QRectF(left, top, shown.width(), shown.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        rect = self._image_rect()
        position = event.position()
        if rect.contains(position) and rect.width() and rect.height():
            self.point_clicked.emit(
                (position.x() - rect.left()) / rect.width(),
                (position.y() - rect.top()) / rect.height(),
            )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._marker is None:
            return
        rect = self._image_rect()
        point = QPointF(
            rect.left() + self._marker[0] * rect.width(),
            rect.top() + self._marker[1] * rect.height(),
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#c6f05b"), 3))
        painter.drawEllipse(point, 12, 12)
        painter.drawLine(point + QPointF(-18, 0), point + QPointF(18, 0))
        painter.drawLine(point + QPointF(0, -18), point + QPointF(0, 18))


class SnapCenterAnchorWindow(QMainWindow):
    def __init__(self, store: AnchorLabelStore) -> None:
        super().__init__()
        self.store = store
        self.index = self._first_pending_index()
        self.setWindowTitle("TapeSift - Center / Ball Anchor Review")
        self.resize(1380, 900)

        root = QWidget(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        header = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            "font:700 18px Georgia;color:#ecf3e8"
        )
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.progress_label = QLabel()
        self.progress_label.setStyleSheet(
            "font:700 13px Consolas;color:#c6f05b"
        )
        header.addWidget(self.progress_label)
        outer.addLayout(header)

        self.instruction_label = QLabel(
            "Click the center / football exchange point on the large -250 ms "
            "frame. Use Occluded or Unsure only when a point cannot be placed."
        )
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setStyleSheet("color:#bcc9bf;font-size:14px")
        outer.addWidget(self.instruction_label)

        self.anchor_image = AnchorImage(self)
        self.anchor_image.point_clicked.connect(self._mark_visible)
        outer.addWidget(self.anchor_image, 1)

        strip = QHBoxLayout()
        strip.setSpacing(8)
        self.context_labels: dict[str, QLabel] = {}
        for offset in ("-500", "-250", "0", "250"):
            card = QFrame()
            card.setStyleSheet(
                "QFrame{background:#172019;border:1px solid #344239}"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(5, 5, 5, 5)
            caption = QLabel(f"{int(offset):+d} ms")
            caption.setStyleSheet("font:700 12px Consolas;color:#c6f05b")
            card_layout.addWidget(caption)
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setMinimumHeight(115)
            card_layout.addWidget(image)
            self.context_labels[offset] = image
            strip.addWidget(card, 1)
        outer.addLayout(strip)

        actions = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.previous_button.clicked.connect(lambda: self._move(-1))
        actions.addWidget(self.previous_button)
        self.occluded_button = QPushButton("Occluded")
        self.occluded_button.clicked.connect(
            lambda: self._mark_unavailable("occluded")
        )
        actions.addWidget(self.occluded_button)
        self.unsure_button = QPushButton("Unsure")
        self.unsure_button.clicked.connect(lambda: self._mark_unavailable("unsure"))
        actions.addWidget(self.unsure_button)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self._clear)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        self.next_button = QPushButton("Save & Next")
        self.next_button.setStyleSheet(
            "background:#c6f05b;color:#101710;font-weight:800;padding:8px 18px"
        )
        self.next_button.clicked.connect(self._save_and_next)
        actions.addWidget(self.next_button)
        outer.addLayout(actions)

        root.setStyleSheet(
            "QWidget{background:#101611;color:#ecf3e8}"
            "QPushButton{background:#27332b;border:1px solid #526158;"
            "padding:8px 13px;color:#ecf3e8}"
            "QPushButton:hover{border-color:#c6f05b}"
        )
        self.statusBar().setStyleSheet(
            "background:#172019;color:#c4d0c7;padding:3px"
        )
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._move(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._move(1))
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=self._save_and_next)
        QShortcut(QKeySequence("O"), self, activated=lambda: self._mark_unavailable("occluded"))
        QShortcut(QKeySequence("U"), self, activated=lambda: self._mark_unavailable("unsure"))
        self._render()

    def _first_pending_index(self) -> int:
        for index, record in enumerate(self.store.records):
            if str(record["review_item_id"]) not in self.store.labels:
                return index
        return 0

    def _record(self) -> dict[str, Any]:
        return self.store.records[self.index]

    def _render(self) -> None:
        record = self._record()
        review_item_id = str(record["review_item_id"])
        label = self.store.labels.get(review_item_id)
        priority = "7E FAILURE" if record.get("priority_failure") else "DEVELOPMENT"
        self.title_label.setText(
            f"{priority}  |  {record['project_name']}  |  "
            f"Clip {int(record['clip_number']):02d}  |  Angle {record['angle']}"
        )
        self.progress_label.setText(
            f"{len(self.store.labels)} / {len(self.store.records)} labeled  |  "
            f"item {self.index + 1}"
        )
        marker = None
        if label and label["anchor_status"] == "visible":
            marker = (float(label["anchor_x"]), float(label["anchor_y"]))
        assets = record["assets"]
        base = self.store.package_path.parent
        self.anchor_image.set_image(base / assets["-250"]["path"], marker)
        for offset, image_label in self.context_labels.items():
            pixmap = QPixmap(str(base / assets[offset]["path"]))
            image_label.setPixmap(pixmap.scaled(
                300,
                150,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        status = label["anchor_status"] if label else "pending"
        self.statusBar().showMessage(
            f"Status: {status} | Autosaves directly to {self.store.labels_path}"
        )
        self.previous_button.setEnabled(self.index > 0)

    def _mark_visible(self, x: float, y: float) -> None:
        review_item_id = str(self._record()["review_item_id"])
        self.store.mark_visible(review_item_id, x, y)
        self.anchor_image.set_marker((x, y))
        self._render()

    def _mark_unavailable(self, status: str) -> None:
        self.store.mark_unavailable(str(self._record()["review_item_id"]), status)
        self._render()

    def _clear(self) -> None:
        self.store.clear(str(self._record()["review_item_id"]))
        self._render()

    def _move(self, amount: int) -> None:
        self.index = max(0, min(len(self.store.records) - 1, self.index + amount))
        self._render()

    def _save_and_next(self) -> None:
        if str(self._record()["review_item_id"]) not in self.store.labels:
            self.statusBar().showMessage(
                "Place an anchor or choose Occluded / Unsure before continuing."
            )
            return
        self._move(1)


def launch_native_review(package_path: Path, labels_path: Path | None = None) -> int:
    application = QApplication.instance() or QApplication([])
    store = AnchorLabelStore(package_path, labels_path)
    window = SnapCenterAnchorWindow(store)
    window.show()
    window.raise_()
    window.activateWindow()
    return application.exec()
