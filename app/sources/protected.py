import re
from urllib.parse import urlparse

import httpx

from app.core.models import AnalysisResult
from app.sources.base import BaseSourceAdapter
from app.utils.logger import get_logger

log = get_logger("vanta.sources.protected")

_PROTECTED_PATTERNS = [
    re.compile(r"/login", re.I),
    re.compile(r"/auth", re.I),
    re.compile(r"account", re.I),
    re.compile(r"signin", re.I),
    re.compile(r"signup", re.I),
    re.compile(r"oauth", re.I),
    re.compile(r"token", re.I),
]

_CLOUDFRONT_FOOTER = re.compile(r"cloudflare|cf-ray|checking your browser", re.I)
_CAPTCHA_INDICATORS = re.compile(r"captcha|recaptcha|turnstile|g-recaptcha", re.I)


class ProtectedSourceAdapter(BaseSourceAdapter):

    @property
    def name(self) -> str:
        return "Protected Source"

    def can_handle(self, url: str) -> bool:
        domain = urlparse(url).netloc or ""
        for pattern in _PROTECTED_PATTERNS:
            if pattern.search(domain) or pattern.search(urlparse(url).path):
                return True
        return False

    async def analyze(self, url: str) -> AnalysisResult:
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, timeout=httpx.Timeout(30))

            if response.status_code in (401, 403):
                return AnalysisResult(
                    title="Authorization Required",
                    source=self.name,
                    files=[],
                    status="error",
                )

            if _CLOUDFRONT_FOOTER.search(response.text) or _CAPTCHA_INDICATORS.search(response.text):
                return AnalysisResult(
                    title="Access Blocked",
                    source=self.name,
                    files=[],
                    status="error",
                )

            return AnalysisResult(
                title="Protected Content",
                source=self.name,
                files=[],
                status="unsupported",
            )

        except httpx.RequestError as e:
            log.error("ProtectedSourceAdapter request error: %s", e)
            return AnalysisResult(
                title="Connection Error",
                source=self.name,
                files=[],
                status="error",
            )
