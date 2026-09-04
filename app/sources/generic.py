from app.core.models import AnalysisResult, DownloadFile, DownloadError
from app.sources.base import BaseSourceAdapter


class GenericSourceAdapter(BaseSourceAdapter):

    @property
    def name(self) -> str:
        return "Generic Source"

    def can_handle(self, url: str) -> bool:
        return True

    async def analyze(self, url: str) -> AnalysisResult:
        import httpx
        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = parsed.netloc or "unknown"

        return AnalysisResult(
            title=f"Content from {domain}",
            source=self.name,
            files=[],
            status="unsupported",
        )
