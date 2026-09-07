from urllib.parse import urljoin

import httpx

from app.core.models import AnalysisResult, DownloadFile
from app.sources.base import BaseSourceAdapter
from app.sources.html_parser import parse_html
from app.sources.link_classifier import (
    deduplicate_preserve_order,
    extract_filename_from_url,
    is_download_candidate,
    is_fragment_only,
    is_ignored_scheme,
)
from app.utils.logger import get_logger

log = get_logger("vanta.sources.generic")

_USER_AGENT = "VANTA/1.4 (+generic-page-resolver)"
_TIMEOUT = httpx.Timeout(30.0)
_MAX_BYTES = 2 * 1024 * 1024


class GenericSourceAdapter(BaseSourceAdapter):

    @property
    def name(self) -> str:
        return "Generic Page"

    def can_handle(self, url: str) -> bool:
        lowered = (url or "").lower().strip()
        if not lowered:
            return False
        return lowered.startswith(("http://", "https://"))

    async def analyze(self, url: str) -> AnalysisResult:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                response = await client.get(url)
        except httpx.RequestError as e:
            log.error("GenericSourceAdapter request error for %s: %s", url, e)
            return AnalysisResult(
                title="Connection Error",
                source=self.name,
                files=[],
                status="error",
            )

        if response.status_code >= 400:
            return AnalysisResult(
                title=f"HTTP {response.status_code}",
                source=self.name,
                files=[],
                status="error",
            )

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return AnalysisResult(
                title="Non-HTML Response",
                source=self.name,
                files=[],
                status="unsupported",
            )

        html = response.text[:_MAX_BYTES]
        page_url = str(response.url)

        parser = parse_html(html)
        title = parser.title or page_url

        files = self._extract_files(parser.links, page_url)

        return AnalysisResult(
            title=title,
            source=self.name,
            files=files,
            status="ready" if files else "unsupported",
        )

    def _extract_files(self, links, page_url: str) -> list[DownloadFile]:
        candidate_urls: list[str] = []
        candidate_text_by_url: dict[str, str] = {}

        for link in links:
            href = (link.href or "").strip()
            if not href:
                continue

            if is_ignored_scheme(href):
                continue
            if is_fragment_only(href):
                continue
            if not is_download_candidate(href):
                continue

            absolute = urljoin(page_url, href)
            candidate_urls.append(absolute)
            if link.text and absolute not in candidate_text_by_url:
                candidate_text_by_url[absolute] = link.text.strip()

        deduped = deduplicate_preserve_order(candidate_urls)

        files: list[DownloadFile] = []
        for absolute in deduped:
            name = candidate_text_by_url.get(absolute) or extract_filename_from_url(absolute) or "download"
            files.append(DownloadFile(name=name, url=absolute))

        return files