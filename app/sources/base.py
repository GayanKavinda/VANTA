from abc import ABC, abstractmethod
from app.core.models import AnalysisResult, ResolvedResource
from app.sources.capability import SourceCapability, SourceType, SourceCapabilities


class BaseSourceAdapter(ABC):

    @property
    def source_type(self) -> SourceType:
        return SourceType.UNKNOWN

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source_type=SourceType.UNKNOWN,
            capabilities=frozenset({SourceCapability.UNSUPPORTED}),
        )

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        pass

    @abstractmethod
    async def analyze(self, url: str) -> AnalysisResult:
        pass

    def last_resolutions(self) -> list[ResolvedResource]:
        """Return the resolutions produced by the most recent analyze() call.

        Default implementation returns an empty list. Adapters that perform
        resolution should override and cache their resolutions.
        """
        return []
