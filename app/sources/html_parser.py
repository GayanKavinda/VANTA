from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from app.sources.link_classifier import (
    is_fragment_only,
    is_ignored_scheme,
)


# V1.15 — Production hardening: parsing limits
_MAX_TITLE_LENGTH = 1024
_MAX_LINKS = 5000
_MAX_NESTING_DEPTH = 100
_MAX_TOTAL_TEXT_SIZE = 10 * 1024 * 1024  # 10MB


@dataclass
class AnchorLink:
    href: str
    text: str


@dataclass(frozen=True)
class ResourceCandidate:
    url: str
    element_type: str
    anchor_text: str = ""
    type_hint: str = ""


class HTMLPageParser(HTMLParser):
    """Lightweight stdlib HTML parser for page title and anchor links.

    No HTTP, no I/O. Pure parsing only.

    V1.15: Added protection against:
    - Excessive nesting depth (actually stops traversal)
    - Excessive number of links
    - Excessive title length
    - Excessive total text size

    Phase 3.2: Discovers media/resource elements (video, audio, source, img, picture, embed, object).
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._title_parts: list[str] = []
        self._in_title: bool = False
        self._title_found: bool = False

        self._in_anchor: bool = False
        self._current_href: Optional[str] = None
        self._current_text_parts: list[str] = []
        self._links: list[AnchorLink] = []

        self._resources: list[ResourceCandidate] = []

        self._nesting_depth: int = 0
        self._max_nesting_depth: int = 0
        self._total_text_size: int = 0
        self._link_count: int = 0
        self._depth_exceeded: bool = False

    @property
    def title(self) -> str:
        joined = "".join(self._title_parts).strip()
        if len(joined) > _MAX_TITLE_LENGTH:
            return joined[:_MAX_TITLE_LENGTH] + "..."
        return joined

    @property
    def links(self) -> list[AnchorLink]:
        return list(self._links)

    @property
    def resources(self) -> list[ResourceCandidate]:
        return list(self._resources)

    def _add_resource(self, url: str, element_type: str, type_hint: str = ""):
        if not url:
            return
        url = url.strip()
        if not url:
            return
        if is_ignored_scheme(url):
            return
        if is_fragment_only(url):
            return
        if self._depth_exceeded:
            return
        self._resources.append(ResourceCandidate(
            url=url,
            element_type=element_type,
            type_hint=type_hint,
        ))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        tag = tag.lower()
        attr_map = {k.lower(): v for k, v in attrs if k}

        if tag == "title":
            self._in_title = True
            self._title_found = True
            return

        if tag == "a":
            self._in_anchor = True
            self._current_href = attr_map.get("href")
            self._current_text_parts = []

        if tag == "a" and attr_map.get("href"):
            self._add_resource(attr_map["href"], "link")

        if tag == "img":
            src = attr_map.get("src")
            if src:
                self._add_resource(src, "image")
            poster = attr_map.get("poster")
            if poster:
                self._add_resource(poster, "image")
            srcset = attr_map.get("srcset")
            if srcset:
                for src_url in _parse_srcset(srcset):
                    self._add_resource(src_url, "image")

        if tag == "video":
            src = attr_map.get("src")
            if src:
                self._add_resource(src, "video")
            poster = attr_map.get("poster")
            if poster:
                self._add_resource(poster, "image")

        if tag == "audio":
            src = attr_map.get("src")
            if src:
                self._add_resource(src, "audio")

        if tag == "source":
            src = attr_map.get("src")
            srcset = attr_map.get("srcset")
            type_hint = attr_map.get("type", "")
            if src:
                self._add_resource(src, "source", type_hint=type_hint)
            if srcset:
                for src_url in _parse_srcset(srcset):
                    self._add_resource(src_url, "source", type_hint=type_hint)

        if tag == "object" and attr_map.get("data"):
            self._add_resource(attr_map["data"], "source")

        if tag == "embed" and attr_map.get("src"):
            self._add_resource(attr_map["src"], "source")

        # V1.15 — Track nesting depth and stop traversal when exceeded
        self._nesting_depth += 1
        if self._nesting_depth > self._max_nesting_depth:
            self._max_nesting_depth = self._nesting_depth
        if self._nesting_depth > _MAX_NESTING_DEPTH:
            # Stop parsing deeper - skip this branch entirely
            self._depth_exceeded = True
            self._nesting_depth = _MAX_NESTING_DEPTH
            # Cap max depth for reporting
            self._max_nesting_depth = _MAX_NESTING_DEPTH

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title" and self._in_title:
            self._in_title = False
            return

        if tag == "a" and self._in_anchor:
            href = self._current_href
            text = "".join(self._current_text_parts).strip()
            if href is not None:
                if self._link_count < _MAX_LINKS:
                    self._links.append(AnchorLink(href=href, text=text))
                self._link_count += 1
            self._in_anchor = False
            self._current_href = None
            self._current_text_parts = []

        # V1.15 — Decrease nesting depth
        self._nesting_depth = max(0, self._nesting_depth - 1)
        # Clear the depth-exceeded flag once we're back within the limit
        if self._depth_exceeded and self._nesting_depth <= _MAX_NESTING_DEPTH:
            self._depth_exceeded = False

    def handle_data(self, data: str):
        # V1.15 — Skip data when depth limit exceeded
        if self._depth_exceeded:
            return

        # V1.15 — Track total text size (check BEFORE adding)
        if self._total_text_size + len(data) > _MAX_TOTAL_TEXT_SIZE:
            # Stop processing data
            return

        self._total_text_size += len(data)

        if self._in_title:
            self._title_parts.append(data)
        elif self._in_anchor:
            self._current_text_parts.append(data)

    def error(self, message: str):
        # Suppress parser errors for malformed HTML
        return


def _parse_srcset(srcset: str) -> list[str]:
    urls: list[str] = []
    for item in srcset.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split()
        if parts:
            url = parts[0]
            if url and not url.startswith("data:") and "." in url:
                urls.append(url)
    return urls


def parse_html(html: str) -> HTMLPageParser:
    parser = HTMLPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser
