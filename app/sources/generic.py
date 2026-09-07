from urllib.parse import urljoin

import httpx

from app.core.models import AnalysisResult, DownloadFile
from app.sources.base import BaseSourceAdapter
from app.services.resolver import Resolver
from app.sources.html_parser import parse_html
from app.sources.link_classifier import (
    deduplicate_preserve_order,
    extract_filename_from_url,
    is_download_candidate,
    is_fragment_only,
    is_ignored_scheme,
    normalize_url,
)
from app.utils.logger import get_logger

log = get_logger("vanta.sources.generic")

_USER_AGENT = "VANTA/1.6 (+generic-page-resolver)"
_TIMEOUT = httpx.Timeout(30.0)
_MAX_BYTES = 2 * 1024 * 1024
_PROBE_CONCURRENCY = 4


def _is_non_resource_path(href: str) -> bool:
    if not href:
        return True
    from urllib.parse import urlparse
    path = (urlparse(href).path or "").lower()
    segments = (
        "/about", "/contact", "/login", "/register", "/signup",
        "/privacy", "/terms", "/help", "/faq", "/search",
        "/category", "/tag", "/author", "/archive",
    )
    return any(path.startswith(seg) or f"{seg}/" in path for seg in segments)


class GenericSourceAdapter(BaseSourceAdapter):

    def __init__(
        self,
        resolver: Resolver | None = None,
    ):
        self._resolver = resolver or Resolver(concurrency=_PROBE_CONCURRENCY)

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

        files = await self._discover_files(parser.links, page_url)

        return AnalysisResult(
            title=title,
            source=self.name,
            files=files,
            status="ready" if files else "unsupported",
        )

    async def _discover_files(self, links, page_url: str) -> list[DownloadFile]:
        obvious: list[dict] = []
        ambiguous: list[dict] = []
        text_by_url: dict[str, str] = {}

        for link in links:
            href = (link.href or "").strip()
            if not href:
                continue
            if is_ignored_scheme(href):
                continue
            if is_fragment_only(href):
                continue
            if _is_non_resource_path(href):
                continue

            absolute = urljoin(page_url, href)
            normalized = normalize_url(absolute)

            if is_download_candidate(href):
                obvious.append({"url": normalized, "anchor_text": link.text or ""})
            else:
                ambiguous.append({"url": normalized, "anchor_text": link.text or ""})

            if link.text:
                text_by_url.setdefault(normalized, link.text.strip())

        seen: set[str] = set()
        combined: list[dict] = []
        for c in obvious + ambiguous:
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            combined.append(c)

        resolved = await self._resolver.resolve_candidates(combined)

        files: list[DownloadFile] = []
        for r in resolved:
            url_filename = extract_filename_from_url(r.final_url)
            filename = (
                r.filename
                or url_filename
                or text_by_url.get(r.source_url)
                or "download"
            )
            files.append(
                DownloadFile(
                    name=filename,
                    url=r.final_url,
                    size=r.size,
                    content_type=r.content_type,
                )
            )

        return files