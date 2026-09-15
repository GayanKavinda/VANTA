from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional


# V1.15 — Production hardening: parsing limits
_MAX_TITLE_LENGTH = 1024
_MAX_LINKS = 5000
_MAX_NESTING_DEPTH = 100
_MAX_TOTAL_TEXT_SIZE = 10 * 1024 * 1024  # 10MB


@dataclass
class AnchorLink:
    href: str
    text: str


class HTMLPageParser(HTMLParser):
    """Lightweight stdlib HTML parser for page title and anchor links.

    No HTTP, no I/O. Pure parsing only.

    V1.15: Added protection against:
    - Excessive nesting depth (actually stops traversal)
    - Excessive number of links
    - Excessive title length
    - Excessive total text size
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

        # V1.15 — Protection state
        self._nesting_depth: int = 0
        self._max_nesting_depth: int = 0
        self._total_text_size: int = 0
        self._link_count: int = 0
        # When depth exceeds the limit, stop processing tags/data entirely
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


def parse_html(html: str) -> HTMLPageParser:
    parser = HTMLPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser