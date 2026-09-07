import asyncio

import httpx

from app.core.models import ResourceProbeResult
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

_USER_AGENT = "VANTA/1.5 (+resource-intelligence)"
_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=10.0)
_GET_PEEK_BYTES = 4096


def _make_failure(url: str, status_code: int = 0) -> ResourceProbeResult:
    return ResourceProbeResult(
        url=url,
        final_url=url,
        status_code=status_code,
        is_downloadable=False,
    )


class ResourceProbe:
    """Lightweight HTTP resource probe.

    HEAD-first, GET-fallback for servers that reject HEAD. The GET fallback
    uses streaming and only reads a tiny initial peek so it never downloads
    a large binary resource into memory just to inspect headers.
    """

    def __init__(
        self,
        *,
        user_agent: str = _USER_AGENT,
        timeout: httpx.Timeout | None = None,
        get_peek_bytes: int = _GET_PEEK_BYTES,
    ):
        self._user_agent = user_agent
        self._timeout = timeout or _TIMEOUT
        self._get_peek_bytes = get_peek_bytes

    async def probe(self, url: str) -> ResourceProbeResult:
        if not url:
            return _make_failure(url or "", status_code=0)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            ) as client:
                return await self._probe_with_client(client, url)
        except httpx.RequestError as e:
            log.warning("ResourceProbe request error for %s: %s", url, e)
            return _make_failure(url, status_code=0)

    async def _probe_with_client(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> ResourceProbeResult:
        try:
            response = await client.head(url)
        except httpx.RequestError as e:
            log.warning("HEAD failed for %s: %s", url, e)
            return _make_failure(url, status_code=0)

        if response.status_code in (
            httpx.codes.METHOD_NOT_ALLOWED,
            httpx.codes.NOT_IMPLEMENTED,
        ):
            try:
                response = await self._peek_get(client, url)
            except httpx.RequestError as e:
                log.warning("GET fallback failed for %s: %s", url, e)
                return _make_failure(url, status_code=0)

        return self._build_result(url, response)

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

    def _build_result(self, original_url: str, response: httpx.Response) -> ResourceProbeResult:
        final_url = str(response.url) or original_url
        status_code = response.status_code
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
            status_code=status_code,
        )

        return ResourceProbeResult(
            url=original_url,
            final_url=final_url,
            status_code=status_code,
            content_type=response.headers.get("content-type"),
            size=size,
            filename=filename,
            supports_range=supports_range,
            is_downloadable=is_downloadable,
        )


async def probe_many(
    urls: list[str],
    *,
    concurrency: int = 4,
    probe: ResourceProbe | None = None,
) -> list[ResourceProbeResult]:
    """Probe multiple URLs with bounded concurrency."""
    if not urls:
        return []

    semaphore = asyncio.Semaphore(max(1, concurrency))
    probe_service = probe or ResourceProbe()

    async def _one(u: str) -> ResourceProbeResult:
        async with semaphore:
            return await probe_service.probe(u)

    return await asyncio.gather(*[_one(u) for u in urls])