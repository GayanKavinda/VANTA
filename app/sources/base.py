from abc import ABC, abstractmethod
from app.core.models import AnalysisResult


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
