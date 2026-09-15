"""V2.0 — Authoritative application version.

Single source of truth for the VANTA version string.
All modules and tests must import VERSION from here.
"""

VERSION = "2.0.0-dev"
"""The current development version of VANTA.

This is the only place the version string is defined.
Do not hard-code version strings elsewhere in the project.
"""

VERSION_MAJOR = 2
VERSION_MINOR = 0
VERSION_PATCH = 0
VERSION_SUFFIX = "dev"
"""Parsed version components used for structured version comparisons."""

__all__ = [
    "VERSION",
    "VERSION_MAJOR",
    "VERSION_MINOR",
    "VERSION_PATCH",
    "VERSION_SUFFIX",
]