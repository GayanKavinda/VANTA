import httpx

from app.core.models import AnalysisResult, DownloadFile
from app.sources.base import BaseSourceAdapter
from app.sources.http_headers import (
    extract_filename_from_content_disposition,
    extract_filename_from_url,
    safe_content_length,
)
from app.utils.logger import get_logger

log = get_logger("vanta.sources.direct")

_TIMEOUT = httpx.Timeout(30.0)


class DirectDownloadAdapter(BaseSourceAdapter):

    @property
    def name(self) -> str:
        return "Direct Download"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False

        from app.sources.http_headers import has_download_extension
        return has_download_extension(url)

    async def analyze(self, url: str) -> AnalysisResult:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_TIMEOUT,
        ) as client:
            response = await client.head(url)

            if response.status_code == httpx.codes.METHOD_NOT_ALLOWED:
                response = await client.get(url)

            response.raise_for_status()

            final_url = str(response.url)
            content_disposition = response.headers.get("content-disposition", "")
            content_type = response.headers.get("content-type")
            size = safe_content_length(response.headers.get("content-length"))

        filename = (
            extract_filename_from_content_disposition(content_disposition)
            or extract_filename_from_url(final_url)
            or "download"
        )

        file = DownloadFile(
            name=filename,
            url=final_url,
            size=size,
            content_type=content_type,
        )

        return AnalysisResult(
            title=filename,
            source=self.name,
            files=[file],
            status="ready",
        )