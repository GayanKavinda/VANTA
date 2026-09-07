import asyncio
from dataclasses import replace

import httpx

from app.core.models import ResourceProbeResult
from app.services.url_security import (
    UrlSecurityDecision,
    is_safe_redirect,
    validate_url,
)
from app.sources.http_headers import (
    classify_downloadability,
    extract_filename_from_content_disposition,
    extract_filename_from_url,
    normalize_media_type,
    parse_accept_ranges,
    safe_content_length,
)
from app.utils.logger import get_logger

log = get_logger("vanta.services.resource_probe")

_USER_AGENT = "VANTA/1.6 (+resource-intelligence)"
_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=10.0)
_GET_PEEK_BYTES = 4096
_DEFAULT_MAX_REDIRECTS = 5


def _make_failure(url: str, status_code: int = 0) -> ResourceProbeResult:
    return ResourceProbeResult(
        url=url,
        final_url=url,
        status_code=status_code,
        is_downloadable=False,
    )


class ResourceProbe:
    """Lightweight HTTP resource probe.

    Uses manual redirect handling so that every hop is validated by
    `is_safe_redirect` BEFORE the next request is sent. This prevents the
    probe from being tricked into following redirects into private /
    loopback / blocked destinations.

    HEAD-first. Falls back to a streaming GET peek for servers that reject
    HEAD. The GET fallback reads only a tiny initial chunk and closes
    the response; it never buffers a full binary resource.
    """

    def __init__(
        self,
        *,
        user_agent: str = _USER_AGENT,
        timeout: httpx.Timeout | None = None,
        get_peek_bytes: int = _GET_PEEK_BYTES,
        max_redirects: int = _DEFAULT_MAX_REDIRECTS,
        allow_private_networks: bool = False,
        blocked_hosts=(),
    ):
        self._user_agent = user_agent
        self._timeout = timeout or _TIMEOUT
        self._get_peek_bytes = get_peek_bytes
        self._max_redirects = max(0, max_redirects)
        self._allow_private = allow_private_networks
        self._blocked_hosts = tuple(blocked_hosts)

    @property
    def max_redirects(self) -> int:
        return self._max_redirects

    async def probe(self, url: str) -> ResourceProbeResult:
        if not url:
            return _make_failure(url or "", status_code=0)

        initial = validate_url(
            url,
            allow_private_networks=self._allow_private,
            blocked_hosts=self._blocked_hosts,
        )
        if not initial.is_safe:
            log.warning("ResourceProbe rejected initial URL %s: %s", url, initial.reason)
            return _make_failure(url, status_code=0)

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            ) as client:
                return await self._walk(client, url)
        except httpx.RequestError as e:
            log.warning("ResourceProbe request error for %s: %s", url, e)
            return _make_failure(url, status_code=0)

    async def _walk(self, client: httpx.AsyncClient, start_url: str) -> ResourceProbeResult:
        current_url = start_url
        for hop in range(self._max_redirects + 1):
            try:
                response = await client.head(current_url)
            except httpx.RequestError as e:
                log.warning("HEAD failed for %s: %s", current_url, e)
                return _make_failure(current_url, status_code=0)

            if response.status_code in (
                httpx.codes.METHOD_NOT_ALLOWED,
                httpx.codes.NOT_IMPLEMENTED,
            ):
                try:
                    response = await self._peek_get(client, current_url)
                except httpx.RequestError as e:
                    log.warning("GET fallback failed for %s: %s", current_url, e)
                    return _make_failure(current_url, status_code=0)

            if not _is_redirect(response.status_code):
                return self._build_result(start_url, current_url, response)

            if hop >= self._max_redirects:
                log.warning("ResourceProbe redirect limit exceeded for %s", start_url)
                return _make_failure(current_url, status_code=response.status_code)

            location = response.headers.get("location") or response.headers.get("Location")
            if not location:
                return self._build_result(start_url, current_url, response)

            target = _resolve_redirect(current_url, location)
            decision: UrlSecurityDecision = is_safe_redirect(
                current_url,
                target,
                allow_private_networks=self._allow_private,
                blocked_hosts=self._blocked_hosts,
            )
            if not decision.is_safe:
                log.warning(
                    "ResourceProbe rejected redirect %s -> %s: %s",
                    current_url, target, decision.reason,
                )
                return _make_failure(current_url, status_code=response.status_code)

            await response.aclose()
            current_url = target

        return _make_failure(current_url, status_code=0)

    async def _peek_get(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> httpx.Response:
        request = client.build_request("GET", url)
        response = await client.send(request, stream=True)
        try:
            async for _ in response.aiter_bytes(self._get_peek_bytes):
                break
        finally:
            await response.aclose()
        return response

    def _build_result(
        self,
        original_url: str,
        final_url: str,
        response: httpx.Response,
    ) -> ResourceProbeResult:
        content_disposition = response.headers.get("content-disposition", "")
        media_type = normalize_media_type(response.headers.get("content-type"))
        size = safe_content_length(response.headers.get("content-length"))
        supports_range = parse_accept_ranges(response.headers.get("accept-ranges"))
        filename = (
            extract_filename_from_content_disposition(content_disposition)
            or extract_filename_from_url(final_url)
        )
        is_downloadable = classify_downloadability(
            media_type=media_type,
            url=final_url,
            status_code=response.status_code,
        )

        return ResourceProbeResult(
            url=original_url,
            final_url=final_url,
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            size=size,
            filename=filename,
            supports_range=supports_range,
            is_downloadable=is_downloadable,
        )


def _is_redirect(status_code: int) -> bool:
    return status_code in (
        httpx.codes.MOVED_PERMANENTLY,
        httpx.codes.FOUND,
        httpx.codes.SEE_OTHER,
        httpx.codes.TEMPORARY_REDIRECT,
        httpx.codes.PERMANENT_REDIRECT,
    )


def _resolve_redirect(current_url: str, location: str) -> str:
    from urllib.parse import urljoin
    return urljoin(current_url, location)


async def probe_many(
    urls: list[str],
    *,
    concurrency: int = 4,
    probe: ResourceProbe | None = None,
) -> list[ResourceProbeResult]:
    if not urls:
        return []

    semaphore = asyncio.Semaphore(max(1, concurrency))
    probe_service = probe or ResourceProbe()

    async def _one(u: str) -> ResourceProbeResult:
        async with semaphore:
            return await probe_service.probe(u)

    return await asyncio.gather(*[_one(u) for u in urls])