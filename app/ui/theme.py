"""V2.0 Phase 4.10 - Runtime theme switching.

A tiny, UI/application-level theme helper. It does not touch the download
engine, queue, scheduler, security, persistence, history, or scheduling
subsystems. It only loads the appropriate Qt stylesheet onto the
QApplication and persists the active choice through SettingsService.

Theme resources live under assets/styles:

* main.qss  - dark theme (existing, unchanged)
* light.qss - light theme (added in Phase 4.10)
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.services.settings_service import DEFAULTS, SettingsService
from app.utils.logger import get_logger

log = get_logger("vanta.ui.theme")

THEME_DARK = "dark"
THEME_LIGHT = "light"

VALID_THEMES = (THEME_DARK, THEME_LIGHT)

_STYLES_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "styles"
_STYLE_FILES = {
    THEME_DARK: _STYLES_DIR / "main.qss",
    THEME_LIGHT: _STYLES_DIR / "light.qss",
}


def _normalize_theme(value):
    """Return a valid theme name, falling back to the default."""
    if not value:
        return DEFAULTS["theme"]
    normalized = value.strip().lower()
    if normalized in VALID_THEMES:
        return normalized
    log.warning("Unknown theme %r; falling back to %s", value, DEFAULTS["theme"])
    return DEFAULTS["theme"]


def _style_path(theme: str) -> Path:
    return _STYLE_FILES[_normalize_theme(theme)]


def load_style_text(theme: str) -> str:
    """Return the raw QSS text for theme.

    The dark stylesheet is the existing main.qss and is never modified.
    Missing files fall back to an empty string so a broken theme can never
    break application startup.
    """
    path = _style_path(theme)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("Failed to read theme stylesheet %s: %s", path, exc)
        return ""


class ThemeService:
    """Apply and persist the active application theme at runtime."""

    def __init__(self, settings: SettingsService, app=None):
        self._settings = settings
        self._app = app or QApplication.instance()

    @property
    def current(self) -> str:
        return _normalize_theme(self._settings.get("theme", DEFAULTS["theme"]))

    def apply(self, theme=None) -> str:
        """Apply theme (or the persisted theme) to the QApplication.

        Returns the theme name that was actually applied. Safe to call at
        startup and at runtime; never recreates widgets or restarts the app.
        """
        name = _normalize_theme(theme if theme is not None else self.current)
        text = load_style_text(name)
        if self._app is not None:
            self._app.setStyleSheet(text)
        log.info("Theme applied: %s", name)
        return name

    def set(self, theme: str) -> str:
        """Persist theme and apply it immediately.

        Returns the theme name that was actually applied.
        """
        name = _normalize_theme(theme)
        self._settings.set("theme", name)
        return self.apply(name)


__all__ = [
    "ThemeService",
    "THEME_DARK",
    "THEME_LIGHT",
    "VALID_THEMES",
    "load_style_text",
]