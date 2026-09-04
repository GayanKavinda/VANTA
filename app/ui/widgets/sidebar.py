from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSpacerItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.sidebar_button import SidebarButton


class Sidebar(QFrame):
    page_changed = Signal(int)

    def __init__(self):
        super().__init__()

        self._active_index = 0

        self.setObjectName("sidebar")
        self.setFixedWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(8)

        self.buttons: list[SidebarButton] = []

        items = [
            ("Home", 0),
            ("Downloads", 1),
            ("History", 2),
            ("Settings", 3),
        ]

        for text, index in items:
            btn = SidebarButton(text, index)
            btn.clicked.connect(self.page_changed)
            self.buttons.append(btn)
            layout.addWidget(btn)

        self.set_active(0)

    def set_active(self, index: int):
        for i, btn in enumerate(self.buttons):
            btn.set_active(i == index)
        self._active_index = index
