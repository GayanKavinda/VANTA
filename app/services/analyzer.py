from dataclasses import dataclass, field
from typing import Optional

from app.core.models import AnalysisResult, ResolvedResource
from app.services.source_detector import SourceDetector
from app.utils.logger import get_logger

log = get_logger("vanta.services.analyzer")


@dataclass
class ResolutionContext:
    """Rich resolution metadata attached to an analysis result.

    Maps each DownloadFile (by `url` identity) back to its ResolvedResource
    so the UI layer can render confidence, score, and reasons.
    """

    resolutions: list[ResolvedResource] = field(default_factory=list)

    def for_file(self, file: DownloadFile) -> Optional[ResolvedResource]:
        for r in self.resolutions:
            if r.final_url == file.url or r.source_url == file.url:
                return r
        return None


class AnalyzerService:

    def __init__(self, detector: SourceDetector | None = None):
        self._detector = detector or SourceDetector()

    async def analyze(
        self,
        url: str,
    ) -> AnalysisResult:
        result, _ = await self._analyze_internal(url, return_context=False)
        return result

    async def analyze_with_context(
        self,
        url: str,
    ) -> tuple[AnalysisResult, ResolutionContext]:
        return await self._analyze_internal(url, return_context=True)

    async def _analyze_internal(
        self,
        url: str,
        *,
        return_context: bool,
    ) -> tuple[AnalysisResult, ResolutionContext]:
        adapter = self._detector.detect(url)

        if adapter is None:
            log.error("No adapter found for URL: %s", url)
            return (
                AnalysisResult(
                    title="Unsupported Source",
                    source="Unknown",
                    files=[],
                    status="unsupported",
                ),
                ResolutionContext(),
            )

        log.info("Using adapter '%s' for URL: %s", adapter.name, url)

        try:
            result = await adapter.analyze(url)
            log.info(
                "Analysis complete: title=%s, files=%d",
                result.title, len(result.files),
            )
        except Exception as e:
            log.error("Analysis failed for URL '%s': %s", url, e, exc_info=True)
            return (
                AnalysisResult(
                    title="Analysis Failed",
                    source=adapter.name,
                    files=[],
                    status="error",
                ),
                ResolutionContext(),
            )

        context = ResolutionContext()
        if return_context:
            resolutions = self._collect_resolutions(adapter)
            context = ResolutionContext(resolutions=resolutions)

        return result, context

    def _collect_resolutions(self, adapter) -> list[ResolvedResource]:
        getter = getattr(adapter, "last_resolutions", None)
        if not callable(getter):
            return []
        try:
            return list(getter())
        except Exception as e:
            log.warning("Resolution collection failed: %s", e)
            return []
