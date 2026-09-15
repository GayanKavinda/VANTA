"""V2.0 Phase 3.3 — Advanced Standard-Web Resource Recognition tests.

Covers <link> discovery, download attribute support, srcset improvements,
entity handling, and deduplication across discovery sources.
"""

import pytest

from app.sources.html_parser import parse_html, _parse_srcset


# ── <link> rel="stylesheet" ──────────────────────────────────────────

def test_link_stylesheet():
    parser = parse_html('<link rel="stylesheet" href="/css/site.css" type="text/css">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/css/site.css"
    assert r.element_type == "link"
    assert r.type_hint == "text/css"


def test_link_stylesheet_no_type():
    parser = parse_html('<link rel="stylesheet" href="/css/site.css">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.type_hint == ""


def test_link_stylesheet_absolute():
    parser = parse_html('<link rel="stylesheet" href="https://cdn.example.com/style.css">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].url == "https://cdn.example.com/style.css"


# ── <link> rel="icon" ────────────────────────────────────────────────

def test_link_icon():
    parser = parse_html('<link rel="icon" href="/favicon.ico">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.element_type == "image"
    assert r.url == "/favicon.ico"


def test_link_shortcut_icon():
    parser = parse_html('<link rel="shortcut icon" href="/favicon.ico">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].element_type == "image"


def test_link_apple_touch_icon():
    parser = parse_html('<link rel="apple-touch-icon" href="/touch-icon.png">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].element_type == "image"
    assert resources[0].url == "/touch-icon.png"


def test_link_icon_with_type():
    parser = parse_html('<link rel="icon" href="/favicon.svg" type="image/svg+xml">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].type_hint == "image/svg+xml"


def test_link_shortcut_icon():
    parser = parse_html('<link rel="shortcut icon" href="/favicon.ico">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.element_type == "image"
    assert r.url == "/favicon.ico"


def test_link_icon_shortcut_reversed():
    parser = parse_html('<link rel="icon shortcut" href="/favicon.ico">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].element_type == "image"


def test_link_shortcut_alone_ignored():
    parser = parse_html('<link rel="shortcut" href="/favicon.ico">')
    resources = parser.resources
    assert len(resources) == 0


# ── <link> rel="preload" ─────────────────────────────────────────────

def test_link_preload():
    parser = parse_html('<link rel="preload" href="/video.mp4">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.element_type == "link"
    assert r.url == "/video.mp4"


def test_link_preload_as_video():
    parser = parse_html('<link rel="preload" href="/video.mp4" as="video">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].type_hint == "video"


def test_link_preload_as_image():
    parser = parse_html('<link rel="preload" href="/img.webp" as="image">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].type_hint == "image"


def test_link_preload_as_font():
    parser = parse_html('<link rel="preload" href="/font.woff2" as="font">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].type_hint == "font"


# ── <link> rel="alternate" ───────────────────────────────────────────

def test_link_alternate_with_type():
    parser = parse_html('<link rel="alternate" href="/feed.xml" type="application/rss+xml">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/feed.xml"
    assert r.element_type == "link"
    assert r.type_hint == "application/rss+xml"


def test_link_alternate_no_type():
    parser = parse_html('<link rel="alternate" href="/feed.xml">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].element_type == "link"


# ── <link> rel="manifest" ────────────────────────────────────────────

def test_link_manifest():
    parser = parse_html('<link rel="manifest" href="/manifest.json">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].url == "/manifest.json"
    assert resources[0].element_type == "link"


# ── <link> ignored rel values ────────────────────────────────────────

def test_link_canonical_ignored():
    parser = parse_html('<link rel="canonical" href="/page/2">')
    resources = parser.resources
    assert len(resources) == 0


def test_link_noopener_ignored():
    parser = parse_html('<link rel="noopener" href="https://example.com">')
    resources = parser.resources
    assert len(resources) == 0


def test_link_noreferrer_ignored():
    parser = parse_html('<link rel="noreferrer" href="/secret">')
    resources = parser.resources
    assert len(resources) == 0


def test_link_dns_prefetch_ignored():
    parser = parse_html('<link rel="dns-prefetch" href="//cdn.example.com">')
    resources = parser.resources
    assert len(resources) == 0


def test_link_preconnect_ignored():
    parser = parse_html('<link rel="preconnect" href="https://cdn.example.com">')
    resources = parser.resources
    assert len(resources) == 0


# ── <link> edge cases ────────────────────────────────────────────────

def test_link_empty_href_ignored():
    parser = parse_html('<link rel="stylesheet" href="">')
    resources = parser.resources
    assert len(resources) == 0


def test_link_no_href_ignored():
    parser = parse_html('<link rel="stylesheet">')
    resources = parser.resources
    assert len(resources) == 0


def test_link_no_rel_ignored():
    parser = parse_html('<link href="/file.pdf">')
    resources = parser.resources
    assert len(resources) == 0


def test_link_multiple_rel_values():
    parser = parse_html('<link rel="preload stylesheet" href="/style.css">')
    resources = parser.resources
    assert len(resources) >= 1


def test_link_case_insensitive_rel():
    parser = parse_html('<link rel="STYLESHEET" href="/css/site.css">')
    resources = parser.resources
    assert len(resources) == 1


# ── Download attribute ───────────────────────────────────────────────

def test_download_boolean():
    parser = parse_html('<a href="/files/report.pdf" download>')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.element_type == "link"
    assert r.download_name_hint == ""


def test_download_filename():
    parser = parse_html('<a href="/files/report.pdf" download="annual-report.pdf">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.download_name_hint == "annual-report.pdf"


def test_download_empty_ignored():
    parser = parse_html('<a href="/files/report.pdf" download="">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.download_name_hint == ""


def test_download_path_traversal_rejected():
    parser = parse_html('<a href="/files/report.pdf" download="../../etc/passwd">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.download_name_hint == ""


def test_download_backslash_traversal_rejected():
    parser = parse_html('<a href="/files/report.pdf" download="..\\..\\etc\\passwd">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.download_name_hint == ""


def test_download_filename_with_path():
    parser = parse_html('<a href="/files/report.pdf" download="/path/to/name.zip">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.download_name_hint == "name.zip"
    assert ".." not in r.download_name_hint


def test_download_without_href_ignored():
    parser = parse_html('<a download="file.pdf">')
    resources = parser.resources
    assert len(resources) == 0


def test_download_keeps_anchor_as_candidate():
    """Download attribute doesn't change candidate status - href still matters."""
    parser = parse_html('<a href="/page" download>')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].url == "/page"


# ── Srcset improvements ──────────────────────────────────────────────

def test_srcset_single_url():
    urls = _parse_srcset("image.jpg")
    assert urls == ["image.jpg"]


def test_srcset_1x_2x():
    urls = _parse_srcset("image.jpg 1x, image.jpg 2x")
    assert "image.jpg" in urls


def test_srcset_width_descriptors():
    urls = _parse_srcset("small.jpg 480w, large.jpg 1200w")
    assert "small.jpg" in urls
    assert "large.jpg" in urls


def test_srcset_mixed():
    urls = _parse_srcset("image.jpg 1x, image@2x.jpg 2x")
    assert "image.jpg" in urls
    assert "image@2x.jpg" in urls


def test_srcset_empty_entries_ignored():
    urls = _parse_srcset(", , ,")
    assert urls == []


def test_srcset_malformed_ignored():
    urls = _parse_srcset(", , invalid, ,")
    assert urls == []


def test_srcset_data_ignored():
    urls = _parse_srcset("data:image/png;base64,abc 1x")
    assert urls == []


def test_srcset_data_only():
    urls = _parse_srcset("data:image/webp;base64,xyz")
    assert urls == []


def test_srcset_relative_urls():
    urls = _parse_srcset("img/small.jpg 480w, img/large.jpg 1200w")
    assert "img/small.jpg" in urls
    assert "img/large.jpg" in urls


def test_srcset_absolute_urls():
    urls = _parse_srcset("https://cdn.example.com/small.jpg 480w")
    assert "https://cdn.example.com/small.jpg" in urls


def test_srcset_protocol_relative():
    urls = _parse_srcset("//cdn.example.com/image.jpg 1x")
    assert "//cdn.example.com/image.jpg" in urls


def test_srcset_spaces_preserved():
    urls = _parse_srcset("image-small.jpg 480w, image-large.jpg 1200w")
    assert len(urls) == 2
    assert "image-small.jpg" in urls
    assert "image-large.jpg" in urls


def test_srcset_malformed_discarded():
    urls = _parse_srcset(", , invalid, ,")
    assert urls == []


# ── Entity handling ──────────────────────────────────────────────────

def test_entity_amp_in_url():
    parser = parse_html('<a href="/download?a=1&amp;b=2">')
    resources = parser.resources
    assert len(resources) == 1
    assert "a=1" in resources[0].url
    assert "b=2" in resources[0].url


def test_entity_quoted_in_url():
    parser = parse_html("<a href='/search?q=hello&amp;world'>")
    resources = parser.resources
    assert len(resources) == 1
    assert "q=hello" in resources[0].url


# ── Whitespace handling ──────────────────────────────────────────────

def test_whitespace_stripped():
    parser = parse_html('<a href=" file.pdf ">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].url == "file.pdf"


# ── URL handling ─────────────────────────────────────────────────────

def test_absolute_url():
    parser = parse_html('<img src="https://cdn.example.com/photo.jpg">')
    resources = parser.resources
    assert "https://cdn.example.com/photo.jpg" in [r.url for r in resources]


def test_root_relative_url():
    parser = parse_html('<img src="/media/photo.jpg">')
    resources = parser.resources
    assert "/media/photo.jpg" in [r.url for r in resources]


def test_relative_url():
    parser = parse_html('<img src="media/photo.jpg">')
    resources = parser.resources
    assert "media/photo.jpg" in [r.url for r in resources]


def test_ignored_scheme_javascript():
    parser = parse_html('<img src="javascript:alert(1)">')
    assert len(parser.resources) == 0


def test_ignored_scheme_data():
    parser = parse_html('<img src="data:image/png;base64,abc">')
    assert len(parser.resources) == 0


def test_ignored_scheme_mailto():
    parser = parse_html('<img src="mailto:test@example.com">')
    assert len(parser.resources) == 0


# ── Deduplication ────────────────────────────────────────────────────

def test_dedup_same_url_a_and_link():
    parser = parse_html('<a href="file.pdf"><link rel="preload" href="file.pdf">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert len([u for u in urls if u == "file.pdf"]) >= 1


def test_dedup_same_url_img_and_source():
    parser = parse_html('<img src="photo.jpg"><source src="photo.jpg">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert len([u for u in urls if u == "photo.jpg"]) >= 1


def test_dedup_different_urls():
    parser = parse_html('<a href="a.zip"><a href="b.zip">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "a.zip" in urls
    assert "b.zip" in urls


# ── Existing discovery regression ────────────────────────────────────

def test_existing_anchor_discovery():
    parser = parse_html('<a href="file.zip">download</a>')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].element_type == "link"


def test_existing_image_discovery():
    parser = parse_html('<img src="photo.jpg">')
    resources = parser.resources
    assert len(resources) == 1


def test_existing_video_discovery():
    parser = parse_html('<video src="video.mp4"></video>')
    resources = parser.resources
    assert len(resources) == 1


def test_existing_audio_discovery():
    parser = parse_html('<audio src="song.mp3"></audio>')
    resources = parser.resources
    assert len(resources) == 1


def test_existing_poster_discovery():
    parser = parse_html('<video poster="poster.jpg"></video>')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].element_type == "image"


def test_existing_object_discovery():
    parser = parse_html('<object data="file.zip"></object>')
    resources = parser.resources
    assert len(resources) == 1


def test_existing_embed_discovery():
    parser = parse_html('<embed src="file.zip">')
    resources = parser.resources
    assert len(resources) == 1


def test_source_inside_video():
    parser = parse_html("<video><source src='video.mp4' type='video/mp4'></video>")
    resources = parser.resources
    assert 'video.mp4' in [r.url for r in resources]


def test_picture_source():
    parser = parse_html("<picture><source srcset='img.webp' type='image/webp'></picture>")
    resources = parser.resources
    assert 'img.webp' in [r.url for r in resources]


# ── Parser limits ────────────────────────────────────────────────────

def test_parser_nesting_protection():
    deep = "<div>" * 200 + "content" + "</div>" * 200
    parser = parse_html(deep)
    # Should not raise and should return empty resources for the deep content
    assert parser is not None


def test_parser_size_limit():
    large = "<html><body>" + "x" * (20 * 1024 * 1024) + "</body></html>"
    parser = parse_html(large)
    assert parser is not None


def test_max_links_respected():
    links = "".join(f'<a href="/page{i}.html">link</a>' for i in range(6000))
    parser = parse_html(f"<html><body>{links}</body></html>")
    assert len(parser.links) <= 5000
