import os
from urllib.parse import unquote, urlparse

import httpx

from app.core.models import AnalysisResult, DownloadFile
from app.sources.base import BaseSourceAdapter
from app.utils.logger import get_logger

log = get_logger("vanta.sources.direct")

_TIMEOUT = httpx.Timeout(30.0)


class DirectDownloadAdapter(BaseSourceAdapter):

    @property
    def name(self) -> str:
        return "Direct Download"

    def can_handle(self, url: str) -> bool:
        lowered = url.lower()
        return any(
            lowered.endswith(ext)
            for ext in (
                ".zip", ".rar", ".7z", ".tar", ".gz",
                ".exe", ".msi", ".iso", ".img",
                ".pdf", ".mp4", ".mkv", ".mp3",
                ".jpg", ".png", ".gif", ".txt",
                ".deb", ".rpm", ".dmg",
            )
        ) or "download" in lowered or "file=" in lowered

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
            size = int(response.headers.get("content-length", 0)) or None

        filename = self._extract_filename(content_disposition, final_url)

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

    @staticmethod
    def _extract_filename(content_disposition: str, url: str) -> str:
        if content_disposition:
            for part in content_disposition.split(";"):
                part = part.strip()
                if part.startswith("filename="):
                    filename = part[len("filename="):].strip('"')
                    if filename:
                        return os.path.basename(filename)
                if part.startswith("filename*="):
                    raw = part[len("filename*"):].strip('"')
                    if "''" in raw:
                        raw = raw.split("''", 1)[1]
                    if raw:
                        return os.path.basename(unquote(raw))

        path = urlparse(url).path
        basename = unquote(path).split("/")[-1]

        return basename or "download"