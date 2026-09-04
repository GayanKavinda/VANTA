from app.sources.base import BaseSourceAdapter
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

    def register_adapter(self, adapter: BaseSourceAdapter):
        self._adapters.insert(0, adapter)
