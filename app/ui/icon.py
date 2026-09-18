"""VANTA application icon resolution.

Resolves the VANTA window/application icon from the bundled ``assets``
directory, regardless of whether the application is running from the source
repository or from a PyInstaller `--onefile`/onefile bundle.

The on-disk icon is ``assets/icons/vanta.png`` (256x256, used by Qt's
:class:`QIcon`). The matching ``assets/icons/vanta.ico`` (multi-resolution)
is referenced directly by ``VANTA.spec`` for the Windows executable icon.
"""

import sys
from pathlib import Path

from PySide6.QtGui import QIcon

_ICON_FILE = "vanta.png"
_ICO_FILE = "vanta.ico"


def _source_assets_dir() -> Path:
    """Return ``assets/`` for a source checkout.

    Mirrors the path convention already used by ``app/ui/theme.py``.
    """
    return Path(__file__).resolve().parent.parent.parent / "assets"


def assets_dir() -> Path:
    """Return the ``assets`` directory for either source or frozen execution."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets"
    return _source_assets_dir()


def icon_path() -> Path:
    """Return the filesystem path to the Qt application icon (PNG)."""
    return assets_dir() / "icons" / _ICON_FILE


def ico_path() -> Path:
    """Return the filesystem path to the Windows executable icon (ICO)."""
    return assets_dir() / "icons" / _ICO_FILE


def icon() -> QIcon:
    """Return a :class:`QIcon` for the VANTA application icon."""
    return QIcon(str(icon_path()))


__all__ = [
    "assets_dir",
    "ico_path",
    "icon",
    "icon_path",
]
