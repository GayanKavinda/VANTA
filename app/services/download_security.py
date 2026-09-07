"""Manual redirect walking for DownloadManager.

Mirrors ResourceProbe's redirect-walk pattern but tuned for actual downloads:
    - same-origin policy: Range header is dropped when redirect changes host
    - redirect cap configurable
    - every hop validated via is_safe_redirect
"""
from dataclasses import dataclass
from typing import Iterable, Optional

import httpx

from app.services.url_security import UrlSecurityDecision, is_safe_redirect


DEFAULT_MAX_REDIRECTS = 5


@dataclass
class RedirectWalkResult:
    final_url: str
    response: httpx.Response
    hops: int
    changed_host: bool
    redirect_chain: list[str]


class RedirectWalkError(Exception):
    def __init__(self, kind: str, message: str, current_url: str, status_code: int = 0):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.current_url = current_url
        self.status_code = status_code


def _resolve_redirect(current_url: str, location: str) -> str:
    from urllib.parse import urljoin
    return urljoin(current_url, location)


def _is_redirect(status_code: int) -> bool:
    return status_code in (
        httpx.codes.MOVED_PERMANENTLY,
        httpx.codes.FOUND,
        httpx.codes.SEE_OTHER,
        httpx.codes.TEMPORARY_REDIRECT,
        httpx.codes.PERMANENT_REDIRECT,
    )


def _same_host(a: str, b: str) -> bool:
    from urllib.parse import urlsplit
    pa = urlsplit(a)
    pb = urlsplit(b)
    return (pa.scheme or "").lower() == (pb.scheme or "").lower() and \
           (pa.hostname or "").lower() == (pb.hostname or "").lower()


async def walk_redirects(
    client: httpx.AsyncClient,
    request_factory,
    *,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    allow_private_networks: bool = False,
    blocked_hosts: Iterable[str] = (),
) -> RedirectWalkResult:
    """Walk redirects for a single GET/HEAD request.

    request_factory(url) must return an httpx.Request. The caller is responsible
    for attaching Range / other headers consistently; this helper only manages
    redirect chain semantics and host-change detection.

    Raises:
      RedirectWalkError(kind="unsafe_redirect"|"redirect_loop"|"no_location")
    """
    raise_exc: Optional[RedirectWalkError] = None
    current_url: Optional[str] = None
    chain: list[str] = []
    changed_host = False
    initial_host: Optional[str] = None
    response: Optional[httpx.Response] = None

    try:
        request = request_factory()
        current_url = str(request.url)
        initial_host = (httpx.URL(current_url).host or "").lower()
        chain.append(current_url)

        for hop in range(max_redirects + 1):
            response = await client.send(request, stream=True)
            if not _is_redirect(response.status_code):
                final_url = str(response.url) or current_url
                return RedirectWalkResult(
                    final_url=final_url,
                    response=response,
                    hops=hop,
                    changed_host=changed_host,
                    redirect_chain=chain,
                )

            if hop >= max_redirects:
                await response.aclose()
                raise RedirectWalkError(
                    kind="redirect_loop",
                    message=f"Exceeded {max_redirects} redirects",
                    current_url=current_url,
                    status_code=response.status_code,
                )

            location = response.headers.get("location") or response.headers.get("Location")
            await response.aclose()
            if not location:
                raise RedirectWalkError(
                    kind="no_location",
                    message="Redirect with no Location header",
                    current_url=current_url,
                    status_code=response.status_code,
                )

            target = _resolve_redirect(current_url, location)
            decision: UrlSecurityDecision = is_safe_redirect(
                current_url,
                target,
                allow_private_networks=allow_private_networks,
                blocked_hosts=blocked_hosts,
            )
            if not decision.is_safe:
                raise RedirectWalkError(
                    kind="unsafe_redirect",
                    message=f"Unsafe redirect: {decision.reason}",
                    current_url=target,
                    status_code=response.status_code,
                )

            target_host = (httpx.URL(target).host or "").lower()
            if target_host != initial_host:
                changed_host = True

            chain.append(target)
            current_url = target
            request = request_factory(target)

        raise RedirectWalkError(
            kind="redirect_loop",
            message=f"Exceeded {max_redirects} redirects",
            current_url=current_url or "",
            status_code=0,
        )
    except RedirectWalkError:
        raise
    except Exception as e:
        if raise_exc is not None:
            raise raise_exc from e
        raise