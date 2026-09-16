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


# V1.16 — Useful <link> rel values for resource discovery
_USEFUL_LINK_RELS = frozenset({
    "stylesheet",
    "icon",
    "shortcut icon",
    "apple-touch-icon",
    "preload",
    "alternate",
    "manifest",
})


def _sanitize_download_hint(value: str) -> str:
    """Strip path components and reject traversal from HTML download attribute."""
    if not value:
        return ""
    stripped = value.strip()
    if ".." in stripped:
        return ""
    basename = stripped.replace("\\", "/").rsplit("/", 1)[-1]
    if basename != stripped and ".." in basename:
        return ""
    return basename


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
    download_name_hint: str = ""
    discovery_attribute: str = ""


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
        self._pending_anchor_index: int | None = None
        self._pending_anchor_href: str | None = None
        self._pending_anchor_hint: str = ""

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

    def _add_resource(
        self,
        url: str,
        element_type: str,
        type_hint: str = "",
        download_name_hint: str = "",
        discovery_attribute: str = "",
    ):
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
            download_name_hint=download_name_hint,
            discovery_attribute=discovery_attribute,
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
            download_hint = ""
            if attr_map.get("download"):
                download_hint = _sanitize_download_hint(attr_map.get("download"))
            self._add_resource(attr_map["href"], "link", download_name_hint=download_hint)

        if tag == "link":
            href = attr_map.get("href")
            rel = attr_map.get("rel", "")
            if href and rel:
                rel_tokens = {r.strip().lower() for r in rel.split()}
                has_shortcut_icon = {"shortcut", "icon"}.issubset(rel_tokens)
                is_useful = bool(rel_tokens & _USEFUL_LINK_RELS) or has_shortcut_icon
                if is_useful:
                    type_hint = attr_map.get("type", "") or attr_map.get("as", "")
                    download_hint = ""
                    if attr_map.get("download"):
                        download_hint = _sanitize_download_hint(attr_map.get("download"))
                    if "icon" in rel_tokens or has_shortcut_icon or "apple-touch-icon" in rel_tokens:
                        element_type = "image"
                    else:
                        element_type = "link"
                    discovery_attribute = ""
                    if "preload" in rel_tokens:
                        discovery_attribute = "preload"
                    self._add_resource(
                        href,
                        element_type,
                        type_hint=type_hint,
                        download_name_hint=download_hint,
                        discovery_attribute=discovery_attribute,
                    )

        if tag == "img":
            src = attr_map.get("src")
            if src:
                self._add_resource(src, "image")
            poster = attr_map.get("poster")
            if poster:
                self._add_resource(poster, "image")
            srcset = attr_map.get("srcset")
            if srcset:
                for src_url, descriptor in _parse_srcset_with_descriptors(srcset):
                    self._add_resource(
                        src_url,
                        "image",
                        discovery_attribute="srcset",
                    )
            # V2.0 Phase 3.5 — Lazy-loaded image resources
            _collect_lazy_resources(self, attr_map, "image")

        if tag == "video":
            src = attr_map.get("src")
            if src:
                self._add_resource(src, "video")
            poster = attr_map.get("poster")
            if poster:
                self._add_resource(poster, "image")
            # V2.0 Phase 3.5 — Lazy-loaded video resources
            _collect_lazy_resources(self, attr_map, "video")

        if tag == "audio":
            src = attr_map.get("src")
            if src:
                self._add_resource(src, "audio")
            # V2.0 Phase 3.5 — Lazy-loaded audio resources
            _collect_lazy_resources(self, attr_map, "audio")

        if tag == "source":
            src = attr_map.get("src")
            srcset = attr_map.get("srcset")
            type_hint = attr_map.get("type", "")
            if src:
                self._add_resource(src, "source", type_hint=type_hint)
            if srcset:
                for src_url, descriptor in _parse_srcset_with_descriptors(srcset):
                    self._add_resource(
                        src_url,
                        "source",
                        type_hint=type_hint,
                        discovery_attribute="srcset",
                    )
            # V2.0 Phase 3.5 — Lazy-loaded source resources
            _collect_lazy_resources(self, attr_map, "source")

        if tag == "object" and attr_map.get("data"):
            self._add_resource(attr_map["data"], "source")

        if tag == "embed" and attr_map.get("src"):
            self._add_resource(attr_map["src"], "source")

        # V2.0 Phase 3.5 — Metadata resource discovery (og:image, og:video, og:audio)
        if tag == "meta":
            _collect_meta_resources(self, attr_map)

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


# V2.0 Phase 3.5 — Conservative OpenGraph metadata properties that
# represent actual media/resource references (not arbitrary metadata).
_USEFUL_META_PROPERTIES = frozenset({
    "og:image",
    "og:video",
    "og:audio",
    "og:video:secure_url",
    "og:audio:secure_url",
    "og:image:secure_url",
    "twitter:image",
    "twitter:player",
})


def _is_useful_meta_value(value: Optional[str]) -> bool:
    """Return True only when a meta content value is a legitimate URL."""
    if not value:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith("data:"):
        return False
    if lowered.startswith("javascript:"):
        return False
    if stripped.startswith("#"):
        return False
    return True


def _collect_meta_resources(self, attr_map: dict[str, Optional[str]]):
    """Discover resource URLs from conservative OpenGraph metadata."""
    prop = attr_map.get("property", "")
    if not prop:
        return
    prop_lower = prop.strip().lower()
    if prop_lower not in _USEFUL_META_PROPERTIES:
        return
    content = attr_map.get("content")
    if not _is_useful_meta_value(content):
        return
    self._add_resource(
        content.strip(),
        "meta",
        type_hint=prop_lower,
        discovery_attribute="property",
    )


# V2.0 Phase 3.5 — Conservative allowlist of lazy-loading attributes.
# Only these data-* attributes are treated as resource URLs.
_LAZY_ATTRS = frozenset({
    "data-src",
    "data-original",
    "data-lazy-src",
    "data-url",
    "data-file",
    "data-download",
    "data-video",
    "data-audio",
})


def _is_useful_lazy_value(value: Optional[str]) -> bool:
    """Return True only when a lazy attribute value is a legitimate URL/path."""
    if not value:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith("data:"):
        return False
    if lowered.startswith("javascript:"):
        return False
    if stripped.startswith("#"):
        return False
    return True


def _collect_lazy_resources(self, attr_map: dict[str, Optional[str]], element_type: str):
    """Discover lazy-loaded resources from conservative data-* attributes."""
    for attr in _LAZY_ATTRS:
        value = attr_map.get(attr)
        if _is_useful_lazy_value(value):
            self._add_resource(
                value.strip(),
                element_type,
                discovery_attribute=attr,
            )


def _parse_srcset(srcset: str) -> list[str]:
    """Parse a srcset attribute into a list of URLs.

    Supports width descriptors (480w) and density descriptors (1x, 2x).
    Empty entries, data: URLs, and malformed entries are ignored.
    """
    urls: list[str] = []
    for item in srcset.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split()
        if not parts:
            continue
        url = parts[0]
        if not url:
            continue
        if url.lower().startswith("data:"):
            continue
        if "." not in url:
            continue
        urls.append(url)
    return urls


def _parse_srcset_with_descriptors(srcset: str) -> list[tuple[str, str]]:
    """Parse srcset into (url, descriptor) tuples for evidence propagation.

    Returns the same URLs as _parse_srcset but preserves descriptor evidence
    (e.g. '480w', '2x') where present. Used internally by the parser.
    """
    result: list[tuple[str, str]] = []
    for item in srcset.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split()
        if not parts:
            continue
        url = parts[0]
        if not url:
            continue
        if url.lower().startswith("data:"):
            continue
        if "." not in url:
            continue
        descriptor = parts[1] if len(parts) > 1 else ""
        result.append((url, descriptor))
    return result


def parse_html(html: str) -> HTMLPageParser:
    parser = HTMLPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser
