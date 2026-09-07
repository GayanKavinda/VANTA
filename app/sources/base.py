from abc import ABC, abstractmethod
from app.core.models import AnalysisResult, ResolvedResource


class BaseSourceAdapter(ABC):

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
