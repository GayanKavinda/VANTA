from app.core.models import AnalysisResult
from app.services.source_detector import SourceDetector
from app.utils.logger import get_logger

log = get_logger("vanta.services.analyzer")


class AnalyzerService:

    def __init__(self, detector: SourceDetector | None = None):
        self._detector = detector or SourceDetector()

    async def analyze(self, url: str) -> AnalysisResult:
        adapter = self._detector.detect(url)

        if adapter is None:
            log.error("No adapter found for URL: %s", url)
            return AnalysisResult(
                title="Unsupported Source",
                source="Unknown",
                files=[],
                status="unsupported",
            )

        log.info("Using adapter '%s' for URL: %s", adapter.name, url)

        try:
            result = await adapter.analyze(url)
            log.info("Analysis complete: title=%s, files=%d", result.title, len(result.files))
            return result
        except Exception as e:
            log.error("Analysis failed for URL '%s': %s", url, e, exc_info=True)
            return AnalysisResult(
                title="Analysis Failed",
                source=adapter.name,
                files=[],
                status="error",
            )
