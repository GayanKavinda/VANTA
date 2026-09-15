"""V2.0 — Application metadata.

Central metadata for the VANTA desktop application.
Used by the UI, packaging, and tests.
"""

from app.version import VERSION, VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH, VERSION_SUFFIX

APP_NAME = "VANTA"
"""The product name shown to users."""

APP_DESCRIPTION = (
    "VANTA is a desktop download tool that analyzes web pages, "
    "discovers downloadable resources, and lets you pick what to fetch."
)
"""A short user-facing description of the application."""

APP_VENDOR = "VANTA Project"
"""The vendor/author name shown in About dialogs and packaging metadata."""

APP_HOMEPAGE = "https://vanta.example.com"
"""The project homepage URL."""

APP_VERSION = VERSION
"""Full version string (e.g. '2.0.0-dev')."""

APP_VERSION_TUPLE = (VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH)
"""Numeric version tuple for programmatic comparison."""

APP_VERSION_DISPLAY = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}"
"""User-facing version without the development suffix."""

APP_BUILD = VERSION_SUFFIX
"""Build/suffix identifier (e.g. 'dev', 'rc1', or 'final')."""

APP_LICENSE = "Proprietary"
"""License identifier, if applicable."""


def app_version_string() -> str:
    """Return a human-readable version string for About dialogs."""
    if APP_BUILD and APP_BUILD != "final":
        return f"VANTA {APP_VERSION_DISPLAY} ({APP_BUILD})"
    return f"VANTA {APP_VERSION_DISPLAY}"


def about_text() -> str:
    """Return a multi-line About string suitable for display in the UI."""
    return (
        f"{APP_NAME}\n"
        f"Version {APP_VERSION_DISPLAY}\n"
        f"Build: {APP_BUILD}\n"
        f"\n"
        f"{APP_DESCRIPTION}\n"
        f"\n"
        f"© {APP_VENDOR}"
    )


__all__ = [
    "APP_NAME",
    "APP_DESCRIPTION",
    "APP_VENDOR",
    "APP_HOMEPAGE",
    "APP_VERSION",
    "APP_VERSION_TUPLE",
    "APP_VERSION_DISPLAY",
    "APP_BUILD",
    "APP_LICENSE",
    "app_version_string",
    "about_text",
]