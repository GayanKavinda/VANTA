from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional


@dataclass
class AnchorLink:
    href: str
    text: str


class HTMLPageParser(HTMLParser):
    """Lightweight stdlib HTML parser for page title and anchor links.

    No HTTP, no I/O. Pure parsing only.
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

    @property
    def title(self) -> str:
        joined = "".join(self._title_parts).strip()
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

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title" and self._in_title:
            self._in_title = False
            return

        if tag == "a" and self._in_anchor:
            href = self._current_href
            text = "".join(self._current_text_parts).strip()
            if href is not None:
                self._links.append(AnchorLink(href=href, text=text))
            self._in_anchor = False
            self._current_href = None
            self._current_text_parts = []

    def handle_data(self, data: str):
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_anchor:
            self._current_text_parts.append(data)

    def error(self, message: str):
        return


def parse_html(html: str) -> HTMLPageParser:
    parser = HTMLPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser