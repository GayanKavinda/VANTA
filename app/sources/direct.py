import os

from app.core.models import AnalysisResult, DownloadFile
from app.sources.base import BaseSourceAdapter


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
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.head(url, timeout=httpx.Timeout(30))

            if response.status_code == 405:
                async with client.stream(
                    "GET",
                    url,
                    timeout=httpx.Timeout(30),
                ) as response:
                    async for _ in response.aiter_bytes(4096):
                        pass

        content_disposition = response.headers.get("content-disposition", "")
        filename = self._extract_filename(content_disposition, url)
        size = int(response.headers.get("content-length", 0)) or None

        file = DownloadFile(name=filename, url=url, size=size)

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

        from urllib.parse import unquote, urlparse

        path = urlparse(url).path
        basename = unquote(path).split("/")[-1]

        return basename or "download"
