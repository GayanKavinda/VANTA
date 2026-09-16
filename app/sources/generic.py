from urllib.parse import urljoin

import httpx

from app.core.models import AnalysisResult, DownloadFile, ResolvedResource
from app.sources.capability import SourceCapability, SourceType
from app.sources.base import BaseSourceAdapter
from app.services.resolver import Resolver
from app.services.url_security import validate_url
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

log = get_logger("vanta.generic")

_USER_AGENT = "VANTA/1.6 (+generic-page-resolver)"
_TIMEOUT = httpx.Timeout(30.0)
_MAX_BYTES = 2 * 1024 * 1024
_PROBE_CONCURRENCY = 4


def _is_non_resource_path(href: str) -> bool:
    from app.sources.http_headers import has_download_extension
    if has_download_extension(href or ""):
        return False
    if not href:
        return True
    from urllib.parse import urlparse
    path = (urlparse(href).path or "").lower()
    segments = (
        "/about", "/contact", "/login", "/register", "/signup",
        "/privacy", "/terms", "/help", "/faq", "/search",
        "/category", "/tag", "/author", "/archive",
    )
    for seg in segments:
        if path == seg or path.startswith(seg + "/"):
            return True
    return False


def _describe_discovery_path(candidate) -> str:
    """Return a human-readable description of how a resource was discovered."""
    element = candidate.element_type
    attr = candidate.discovery_attribute

    if element in ("img", "image"):
        if attr == "srcset":
            return "image srcset"
        if attr and attr.startswith("data-"):
            return "lazy-loading"
        return "image element"
    if element == "video":
        if attr and attr.startswith("data-"):
            return "lazy-loading"
        return "video element"
    if element == "audio":
        if attr and attr.startswith("data-"):
            return "lazy-loading"
        return "audio element"
    if element == "source":
        if attr == "srcset":
            return "source srcset"
        return "media source element"
    if element == "link":
        if attr == "preload":
            return "preload link"
        return "HTML link"
    if element == "meta":
        return "metadata"
    if element == "object" or element == "embed":
        return "embedded object"
    return ""


class GenericSourceAdapter(BaseSourceAdapter):

    def __init__(
        self,
        resolver: Resolver | None = None,
        *,
        allow_private_networks: bool = False,
        blocked_hosts=(),
    ):
        self._resolver = resolver or Resolver(
            concurrency=_PROBE_CONCURRENCY,
            allow_private_networks=allow_private_networks,
            blocked_hosts=blocked_hosts,
        )
        self._allow_private = allow_private_networks
        self._blocked_hosts = tuple(blocked_hosts)
        self._last_resolutions: list[ResolvedResource] = []

    @property
    def name(self) -> str:
        return "Generic Page"

    @property
    def source_type(self) -> SourceType:
        return SourceType.WEBPAGE

    @property
    def capabilities(self):
        from app.sources.capability import SourceCapabilities

        return SourceCapabilities(
            source_type=SourceType.WEBPAGE,
            capabilities=frozenset({
                SourceCapability.WEBPAGE_DISCOVERY,
                SourceCapability.HTML_LINK_DISCOVERY,
                SourceCapability.MEDIA_ELEMENT_DISCOVERY,
                SourceCapability.RESOURCE_PROBING,
            }),
        )

    def can_handle(self, url: str) -> bool:
        lowered = (url or "").lower().strip()
        if not lowered:
            return False
        return lowered.startswith(("http://", "https://"))

    def last_resolutions(self) -> list[ResolvedResource]:
        return list(self._last_resolutions)

    async def analyze(self, url: str) -> AnalysisResult:
        decision = validate_url(
            url,
            allow_private_networks=self._allow_private,
            blocked_hosts=self._blocked_hosts,
        )
        if not decision.is_safe:
            log.warning("GenericSourceAdapter rejected URL %s: %s", url, decision.reason)
            return AnalysisResult(
                title=f"Rejected: {decision.reason}",
                source=self.name,
                files=[],
                status="error",
            )

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                from app.services.download_security import is_safe_redirect

                current_url = url
                response: httpx.Response | None = None
                for hop in range(6):
                    response = await client.get(current_url)
                    if response.status_code not in (
                        httpx.codes.MOVED_PERMANENTLY,
                        httpx.codes.FOUND,
                        httpx.codes.SEE_OTHER,
                        httpx.codes.TEMPORARY_REDIRECT,
                        httpx.codes.PERMANENT_REDIRECT,
                    ):
                        break
                    if hop >= 5:
                        await response.aclose()
                        return AnalysisResult(
                            title="Redirect loop",
                            source=self.name,
                            files=[],
                            status="error",
                        )
                    location = response.headers.get("location") or response.headers.get("Location")
                    await response.aclose()
                    if not location:
                        return AnalysisResult(
                            title="Redirect without Location",
                            source=self.name,
                            files=[],
                            status="error",
                        )
                    from urllib.parse import urljoin
                    target = urljoin(current_url, location)
                    decision = is_safe_redirect(
                        current_url,
                        target,
                        allow_private_networks=self._allow_private,
                        blocked_hosts=self._blocked_hosts,
                    )
                    if not decision.is_safe:
                        log.warning(
                            "GenericSourceAdapter unsafe redirect %s -> %s: %s",
                            current_url, target, decision.reason,
                        )
                        return AnalysisResult(
                            title=f"Redirect rejected: {decision.reason}",
                            source=self.name,
                            files=[],
                            status="error",
                        )
                    current_url = target

                if response is None:
                    return AnalysisResult(
                        title="No response",
                        source=self.name,
                        files=[],
                        status="error",
                    )
        except httpx.RequestError as e:
            log.error("GenericSourceAdapter request error for %s: %s", url, e)
            return AnalysisResult(
                title="Connection Error",
                source=self.name,
                files=[],
                status="error",
            )

        if response.status_code >= 400:
            await response.aclose()
            return AnalysisResult(
                title=f"HTTP {response.status_code}",
                source=self.name,
                files=[],
                status="error",
            )

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            await response.aclose()
            return AnalysisResult(
                title="Non-HTML Response",
                source=self.name,
                files=[],
                status="unsupported",
            )

        html = response.text[:_MAX_BYTES]
        page_url = str(response.url) or url

        parser = parse_html(html)
        title = parser.title or page_url

        files = await self._discover_files(parser.resources, page_url)

        return AnalysisResult(
            title=title,
            source=self.name,
            files=files,
            status="ready" if files else "unsupported",
        )

    async def _discover_files(self, candidates, page_url: str) -> list[DownloadFile]:
        obvious: list[dict] = []
        ambiguous: list[dict] = []
        text_by_url: dict[str, str] = {}

        download_hint_by_url: dict[str, str] = {}
        candidate_evidence: dict[str, dict[str, str]] = {}
        discovery_paths: dict[str, list[str]] = {}
        for candidate in candidates:
            href = (candidate.url or "").strip()
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

            if candidate.download_name_hint:
                download_hint_by_url.setdefault(normalized, candidate.download_name_hint)

            evidence = {}
            path_desc = _describe_discovery_path(candidate)
            if path_desc:
                discovery_paths.setdefault(normalized, [])
                if path_desc not in discovery_paths[normalized]:
                    discovery_paths[normalized].append(path_desc)

            if candidate.element_type:
                evidence["element_type"] = candidate.element_type
            if candidate.type_hint:
                evidence["type_hint"] = candidate.type_hint
            if candidate.download_name_hint:
                evidence["download_hint"] = candidate.download_name_hint
            if candidate.discovery_attribute:
                evidence["discovery_attribute"] = candidate.discovery_attribute
            if evidence:
                # Merge evidence for duplicate URLs rather than overwriting,
                # so provenance from multiple discovery paths is preserved.
                existing = candidate_evidence.get(normalized, {})
                for key, value in evidence.items():
                    if key not in existing:
                        existing[key] = value
                candidate_evidence[normalized] = existing

            if is_download_candidate(href):
                obvious.append({"url": normalized, "anchor_text": candidate.anchor_text or ""})
            else:
                ambiguous.append({"url": normalized, "anchor_text": candidate.anchor_text or ""})

            if candidate.anchor_text:
                text_by_url.setdefault(normalized, candidate.anchor_text.strip())

        seen: set[str] = set()
        combined: list[dict] = []
        for c in obvious + ambiguous:
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            combined.append(c)

        resolved = await self._resolver.resolve_candidates(combined)
        self._last_resolutions = list(resolved)

        files: list[DownloadFile] = []
        for r in resolved:
            url_filename = extract_filename_from_url(r.final_url)

            # Determine filename and its source (matching Phase 3.4 precedence)
            filename_source = ""
            if r.filename:
                filename = r.filename
                filename_source = r.filename_source or "url"
            elif url_filename:
                filename = url_filename
                filename_source = "url"
            elif download_hint_by_url.get(r.source_url):
                filename = download_hint_by_url[r.source_url]
                filename_source = "download_hint"
            elif text_by_url.get(r.source_url):
                filename = text_by_url[r.source_url]
                filename_source = "anchor_text"
            else:
                filename = "download"
                filename_source = "fallback"

            evidence = candidate_evidence.get(r.source_url, {})
            element_type = evidence.get("element_type", "")
            type_hint = evidence.get("type_hint", "")
            discovery_attribute = evidence.get("discovery_attribute", "")
            paths = discovery_paths.get(r.source_url, [])

            # V2.0 Phase 3.6 — Persist provenance on the resolved resource so
            # the UI can display where this resource came from without
            # re-deriving it from the candidate list.
            r.element_type = element_type
            r.discovery_attribute = discovery_attribute
            r.html_type_hint = type_hint
            r.discovery_paths = paths
            r.filename_source = filename_source or r.filename_source

            if element_type in ("img", "video", "audio", "source", "object", "embed", "picture"):
                r.reasons.append(f"discovered through {element_type} element")
            if element_type == "link":
                r.reasons.append("discovered through HTML link")
            if element_type == "meta":
                r.reasons.append("discovered through metadata")
            if discovery_attribute == "srcset":
                r.reasons.append("discovered through srcset")
            elif discovery_attribute == "preload":
                r.reasons.append("discovered through preload link")
            elif discovery_attribute:
                r.reasons.append(f"discovered through {discovery_attribute} attribute")
            if type_hint:
                r.reasons.append(f"HTML type hint: {type_hint}")

            files.append(
                DownloadFile(
                    name=filename,
                    url=r.final_url,
                    size=r.size,
                    content_type=r.content_type,
                )
            )

        return files