"""Resource resolver.

Coordinates:
    normalize → security check → probe → score → ResolvedResource
"""
import asyncio
from typing import Iterable, Optional

from app.core.models import ResolvedResource, ResourceProbeResult
from app.services.resource_probe import ResourceProbe
from app.services.url_security import (
    UrlSecurityDecision,
    is_safe_redirect,
    validate_url,
)
from app.sources.candidate_scorer import (
    classify,
    filter_accepted,
    score_candidate,
    to_resolved_resource,
)
from app.sources.link_classifier import normalize_url
from app.utils.logger import get_logger

log = get_logger("vanta.services.resolver")


class Resolver:
    def __init__(
        self,
        probe: ResourceProbe | None = None,
        *,
        concurrency: int = 4,
        allow_private_networks: bool = False,
        blocked_hosts: Iterable[str] = (),
    ):
        if probe is None:
            probe = ResourceProbe(
                allow_private_networks=allow_private_networks,
                blocked_hosts=blocked_hosts,
            )
        self._probe = probe
        self._concurrency = max(1, concurrency)
        self._allow_private = allow_private_networks
        self._blocked_hosts = tuple(blocked_hosts)

    @property
    def probe(self) -> ResourceProbe:
        return self._probe

    async def resolve_candidates(
        self,
        candidates: Iterable[dict],
    ) -> list[ResolvedResource]:
        """Resolve candidates.

        Each candidate is a dict: {url, anchor_text (str, optional)}.
        Returns ResolvedResource for accepted ones, in stable order.
        """
        prepared = self._prepare(candidates)
        if not prepared:
            return []

        semaphore = asyncio.Semaphore(self._concurrency)

        async def _one(c):
            async with semaphore:
                return await self._resolve_one(c["url"], c.get("anchor_text", ""))

        results = await asyncio.gather(*[_one(c) for c in prepared])
        return filter_accepted(results)

    def _prepare(self, candidates: Iterable[dict]) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []

        for c in candidates:
            url = (c.get("url") or "").strip()
            anchor = c.get("anchor_text", "") or ""
            if not url:
                continue

            normalized = normalize_url(url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            decision = validate_url(
                normalized,
                allow_private_networks=self._allow_private,
                blocked_hosts=self._blocked_hosts,
            )
            if not decision.is_safe:
                log.debug("Resolver rejected unsafe URL %s: %s", normalized, decision.reason)
                continue

            out.append({"url": normalized, "anchor_text": anchor})

        return out

    async def _resolve_one(self, url: str, anchor_text: str) -> ResolvedResource:
        from app.sources.http_headers import extract_filename_from_url

        if _has_strong_extension_signal(url):
            score_no_probe, reasons_no_probe = score_candidate(url=url, anchor_text=anchor_text)
            resource = ResolvedResource(
                source_url=url,
                final_url=url,
                filename=extract_filename_from_url(url),
                size=None,
                content_type=None,
                supports_range=None,
                score=score_no_probe,
                confidence=classify(score_no_probe),
                reasons=list(reasons_no_probe),
            )
            return resource

        probe_result = await self._probe.probe(url)
        resource = to_resolved_resource(
            source_url=url,
            probe=probe_result,
            anchor_text=anchor_text,
        )

        if not probe_result.is_downloadable and probe_result.status_code >= 400:
            from app.core.models import ConfidenceLevel
            resource.score = 0
            resource.confidence = ConfidenceLevel.REJECTED
            resource.reasons = _merge_reasons([f"HTTP {probe_result.status_code} (non-success)"])

        final_decision = is_safe_redirect(
            url,
            probe_result.final_url or url,
            allow_private_networks=self._allow_private,
            blocked_hosts=self._blocked_hosts,
        )
        if not final_decision.is_safe:
            log.debug(
                "Resolver rejected unsafe redirect for %s -> %s: %s",
                url, probe_result.final_url, final_decision.reason,
            )
            return _rejected(resource, final_decision.reason)

        return resource

    def classify_only(self, url: str, anchor_text: str = "") -> tuple[int, str]:
        score, _ = score_candidate(url=url, anchor_text=anchor_text)
        return score, classify(score)


def _has_strong_extension_signal(url: str) -> bool:
    from app.sources.http_headers import has_download_extension
    return has_download_extension(url)


def _rejected(resource: ResolvedResource, reason: str) -> ResolvedResource:
    from app.core.models import ConfidenceLevel
    resource.score = 0
    resource.confidence = ConfidenceLevel.REJECTED
    resource.reasons = _merge_reasons(resource.reasons, [f"rejected: {reason}"])
    return resource


def _merge_reasons(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for item in lst:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
    return out