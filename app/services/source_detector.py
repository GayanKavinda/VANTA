from app.sources.base import BaseSourceAdapter
from app.sources.capability import SourceCapability, SourceType, SourceCapabilities
from app.sources.direct import DirectDownloadAdapter
from app.sources.protected import ProtectedSourceAdapter
from app.sources.generic import GenericSourceAdapter


class SourceDetector:

    def __init__(self):
        self._adapters: list[BaseSourceAdapter] = [
            DirectDownloadAdapter(),
            ProtectedSourceAdapter(),
            GenericSourceAdapter(),
        ]

    @property
    def adapters(self) -> list[BaseSourceAdapter]:
        return self._adapters

    def detect(self, url: str) -> BaseSourceAdapter | None:
        for adapter in self._adapters:
            if adapter.can_handle(url):
                return adapter
        return None

    def classify(self, url: str) -> SourceCapabilities:
        adapter = self.detect(url)
        if adapter is None:
            return SourceCapabilities(
                source_type=SourceType.UNKNOWN,
                capabilities=frozenset({SourceCapability.UNSUPPORTED}),
            )
        return adapter.capabilities

    def register_adapter(self, adapter: BaseSourceAdapter):
        self._adapters.insert(0, adapter)
