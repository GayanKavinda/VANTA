from urllib.parse import urljoin

import httpx

from app.core.models import AnalysisResult, DownloadFile
from app.sources.base import BaseSourceAdapter
from app.services.resource_probe import ResourceProbe, probe_many
from app.sources.html_parser import parse_html
from app.sources.http_headers import has_download_extension
from app.sources.link_classifier import (
    deduplicate_preserve_order,
    extract_filename_from_url,
    is_download_candidate,
    is_fragment_only,
    is_ignored_scheme,
)
from app.utils.logger import get_logger

log = get_logger("vanta.sources.generic")

_USER_AGENT = "VANTA/1.5 (+generic-page-resolver)"
_TIMEOUT = httpx.Timeout(30.0)
_MAX_BYTES = 2 * 1024 * 1024
_PROBE_CONCURRENCY = 4

_AMBIGUOUS_HINT_PATH_SEGMENTS = (
    "/download", "/file", "/attachment", "/get",
)
_AMBIGUOUS_HINT_QUERY_KEYS = (
    "download", "file", "id", "attachment",
)
_NON_RESOURCE_HINT_SEGMENTS = (
    "/about", "/contact", "/login", "/register", "/signup",
    "/privacy", "/terms", "/help", "/faq", "/search",
    "/category", "/tag", "/author", "/archive",
)


def _is_ambiguous_resource_link(href: str) -> bool:
    if not href:
        return False
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(href)
    path = (parsed.path or "").lower()
    query_keys = {k.lower() for k in parse_qs(parsed.query).keys()}

    for seg in _NON_RESOURCE_HINT_SEGMENTS:
        if path.startswith(seg) or f"{seg}/" in path:
            return False

    for seg in _AMBIGUOUS_HINT_PATH_SEGMENTS:
        if path.startswith(seg) or f"{seg}/" in path:
            return True

    if query_keys & set(_AMBIGUOUS_HINT_QUERY_KEYS):
        return True

    return False


def _is_non_resource_path(href: str) -> bool:
    if not href:
        return True
    from urllib.parse import urlparse
    path = (urlparse(href).path or "").lower()
    return any(path.startswith(seg) or f"{seg}/" in path for seg in _NON_RESOURCE_HINT_SEGMENTS)


class GenericSourceAdapter(BaseSourceAdapter):

    def __init__(
        self,
        probe: ResourceProbe | None = None,
        probe_concurrency: int = _PROBE_CONCURRENCY,
    ):
        self._probe = probe or ResourceProbe()
        self._probe_concurrency = probe_concurrency

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
        absolute_links: list[str] = []
        text_by_url: dict[str, str] = {}
        ambiguous: list[str] = []

        for link in links:
            href = (link.href or "").strip()
            if not href:
                continue
            if is_ignored_scheme(href):
                continue
            if is_fragment_only(href):
                continue

            absolute = urljoin(page_url, href)

            if _is_non_resource_path(href):
                continue

            if is_download_candidate(href):
                absolute_links.append(absolute)
            elif _is_ambiguous_resource_link(href):
                ambiguous.append(absolute)

            if link.text:
                text_by_url.setdefault(absolute, link.text.strip())

        absolute_links = deduplicate_preserve_order(absolute_links)
        ambiguous = deduplicate_preserve_order(ambiguous)

        probe_results = await probe_many(
            ambiguous,
            concurrency=self._probe_concurrency,
            probe=self._probe,
        )

        probed_urls: list[str] = []
        for result in probe_results:
            if result.is_downloadable:
                probed_urls.append(result.final_url or result.url)

        all_urls = deduplicate_preserve_order(absolute_links + probed_urls)

        files: list[DownloadFile] = []
        probe_by_url = {r.final_url or r.url: r for r in probe_results if r.is_downloadable}
        for absolute in all_urls:
            probed = probe_by_url.get(absolute)
            if probed is not None:
                url_filename = extract_filename_from_url(probed.final_url)
                filename = probed.filename or url_filename or text_by_url.get(absolute) or "download"
                files.append(
                    DownloadFile(
                        name=filename,
                        url=probed.final_url,
                        size=probed.size,
                        content_type=probed.content_type,
                    )
                )
                continue

            url_filename = extract_filename_from_url(absolute)
            if url_filename:
                name = url_filename
            else:
                name = text_by_url.get(absolute) or "download"
            files.append(DownloadFile(name=name, url=absolute))

        return files