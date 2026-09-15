"""Source capability model for VANTA.

Provides a canonical framework for classifying what type of source
VANTA is dealing with and what operations that source supports.

This is a descriptive/routing model. It does NOT implement bypass
behavior, access-control circumvention, or content extraction
beyond what existing adapters already perform.
"""

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class SourceType(str, Enum):
    """High-level classification of a source."""

    DIRECT = "direct"
    WEBPAGE = "webpage"
    GENERIC = "generic"
    PROTECTED = "protected"
    UNKNOWN = "unknown"


class SourceCapability(str, Enum):
    """What a source can legitimately provide."""

    DIRECT_RESOURCE = "direct_resource"
    WEBPAGE_DISCOVERY = "webpage_discovery"
    HTML_LINK_DISCOVERY = "html_link_discovery"
    MEDIA_ELEMENT_DISCOVERY = "media_element_discovery"
    RESOURCE_PROBING = "resource_probing"
    DOWNLOAD = "download"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class SourceCapabilities:
    """Immutable descriptor for a source's capabilities.

    Provides ``supports()`` for convenient capability checks.
    """

    source_type: SourceType
    capabilities: FrozenSet[SourceCapability]

    def supports(self, capability: SourceCapability) -> bool:
        return capability in self.capabilities
