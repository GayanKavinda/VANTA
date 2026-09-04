from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


class SidebarButton(QFrame):
    clicked = Signal(int)

    def __init__(self, text: str, index: int):
        super().__init__()

        self._text = text
        self._index = index
        self._active = False

        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)

        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.dot.setStyleSheet("background: transparent; border-radius: 4px;")

        self.label = QLabel(text)
        self.label.setStyleSheet("font-size: 14px;")

        layout.addWidget(self.dot, alignment=Qt.AlignCenter)
        layout.addWidget(self.label, alignment=Qt.AlignCenter)

        self.update_style()

    def set_active(self, active: bool):
        self._active = active
        self.update_style()

    def update_style(self):
        if self._active:
            self.dot.setStyleSheet("background: #E8E8EA; border-radius: 4px;")
            self.label.setStyleSheet("font-size: 14px; font-weight: 600;")
        else:
            self.dot.setStyleSheet("background: transparent; border-radius: 4px;")
            self.label.setStyleSheet("font-size: 14px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._index)
