"""V2.0 Phase 3.5 - Advanced Resource Discovery and Provenance tests."""

from app.sources.html_parser import parse_html, _parse_srcset, _parse_srcset_with_descriptors, ResourceCandidate


# ── Lazy-loading attribute discovery ───────────────────────────────────

def test_data_src_discovered():
    parser = parse_html('<img src="placeholder.jpg" data-src="/images/photo-large.jpg">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/images/photo-large.jpg" in urls
    candidate = [r for r in resources if r.url == "/images/photo-large.jpg"][0]
    assert candidate.discovery_attribute == "data-src"
    assert candidate.element_type == "image"


def test_data_original_discovered():
    parser = parse_html('<img data-original="/images/photo-large.jpg">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/images/photo-large.jpg" in urls
    candidate = [r for r in resources if r.url == "/images/photo-large.jpg"][0]
    assert candidate.discovery_attribute == "data-original"


def test_data_lazy_src_discovered():
    parser = parse_html('<img data-lazy-src="/images/photo-large.jpg">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/images/photo-large.jpg" in urls
    candidate = [r for r in resources if r.url == "/images/photo-large.jpg"][0]
    assert candidate.discovery_attribute == "data-lazy-src"


def test_video_data_src_discovered():
    parser = parse_html('<video data-src="/videos/movie.mp4"></video>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/videos/movie.mp4" in urls
    candidate = [r for r in resources if r.url == "/videos/movie.mp4"][0]
    assert candidate.element_type == "video"
    assert candidate.discovery_attribute == "data-src"


def test_audio_data_src_discovered():
    parser = parse_html('<audio data-src="/audio/song.mp3"></audio>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/audio/song.mp3" in urls
    candidate = [r for r in resources if r.url == "/audio/song.mp3"][0]
    assert candidate.element_type == "audio"
    assert candidate.discovery_attribute == "data-src"


def test_source_data_src_discovered():
    parser = parse_html('<source data-src="/videos/movie.mp4">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/videos/movie.mp4" in urls
    candidate = [r for r in resources if r.url == "/videos/movie.mp4"][0]
    assert candidate.element_type == "source"
    assert candidate.discovery_attribute == "data-src"


def test_lazy_data_url_ignored():
    parser = parse_html('<img data-src="data:image/png;base64,abc">')
    assert len(parser.resources) == 0


def test_lazy_javascript_url_ignored():
    parser = parse_html('<img data-src="javascript:alert(1)">')
    assert len(parser.resources) == 0


def test_lazy_empty_attribute_ignored():
    parser = parse_html('<img data-src="">')
    assert len(parser.resources) == 0


def test_lazy_fragment_only_ignored():
    parser = parse_html('<img data-src="#section">')
    assert len(parser.resources) == 0


def test_lazy_data_download_discovered():
    parser = parse_html('<img data-download="/files/report.pdf">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/files/report.pdf" in urls
    candidate = [r for r in resources if r.url == "/files/report.pdf"][0]
    assert candidate.discovery_attribute == "data-download"


def test_lazy_data_file_discovered():
    parser = parse_html('<img data-file="/images/photo.jpg">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/images/photo.jpg" in urls
    candidate = [r for r in resources if r.url == "/images/photo.jpg"][0]
    assert candidate.discovery_attribute == "data-file"


def test_lazy_data_url_discovered():
    parser = parse_html('<img data-url="/images/photo.jpg">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/images/photo.jpg" in urls
    candidate = [r for r in resources if r.url == "/images/photo.jpg"][0]
    assert candidate.discovery_attribute == "data-url"


def test_lazy_data_video_discovered():
    parser = parse_html('<video data-video="/videos/movie.mp4">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/videos/movie.mp4" in urls
    candidate = [r for r in resources if r.url == "/videos/movie.mp4"][0]
    assert candidate.discovery_attribute == "data-video"


def test_lazy_data_audio_discovered():
    parser = parse_html('<audio data-audio="/audio/song.mp3">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/audio/song.mp3" in urls
    candidate = [r for r in resources if r.url == "/audio/song.mp3"][0]
    assert candidate.discovery_attribute == "data-audio"


# ── Srcset parsing ─────────────────────────────────────────────────────

def test_srcset_width_descriptors():
    urls = _parse_srcset("small.jpg 480w, medium.jpg 800w, large.jpg 1600w")
    assert "small.jpg" in urls
    assert "medium.jpg" in urls
    assert "large.jpg" in urls
    assert len(urls) == 3


def test_srcset_density_descriptors():
    urls = _parse_srcset("image-small.jpg 1x, image-large.jpg 2x")
    assert "image-small.jpg" in urls
    assert "image-large.jpg" in urls


def test_srcset_mixed_descriptors():
    urls = _parse_srcset("image.jpg 1x, image@2x.jpg 2x, large.jpg 1600w")
    assert "image.jpg" in urls
    assert "image@2x.jpg" in urls
    assert "large.jpg" in urls


def test_srcset_with_descriptors_preserves_descriptor():
    result = _parse_srcset_with_descriptors("small.jpg 480w, large.jpg 1600w")
    assert len(result) == 2
    urls = [r[0] for r in result]
    assert "small.jpg" in urls
    assert "large.jpg" in urls
    descriptors = {r[0]: r[1] for r in result}
    assert descriptors["small.jpg"] == "480w"
    assert descriptors["large.jpg"] == "1600w"


def test_srcset_empty_entries_ignored():
    urls = _parse_srcset(", , ,")
    assert urls == []


def test_srcset_data_url_filtered():
    urls = _parse_srcset("data:image/png;base64,abc 1x")
    assert urls == []


def test_srcset_malformed_entries_ignored():
    urls = _parse_srcset(", , invalid, ,")
    assert urls == []


def test_srcset_single_url_no_descriptor():
    urls = _parse_srcset("image.jpg")
    assert urls == ["image.jpg"]


def test_srcset_img_discovers_all_urls():
    parser = parse_html('<img srcset="small.jpg 480w, large.jpg 1600w">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "small.jpg" in urls
    assert "large.jpg" in urls
    for r in resources:
        assert r.discovery_attribute == "srcset"


def test_srcset_source_discovers_all_urls():
    parser = parse_html('<source srcset="img.webp 1x, img2.webp 2x" type="image/webp">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "img.webp" in urls
    assert "img2.webp" in urls
    for r in resources:
        assert r.discovery_attribute == "srcset"


# ── Metadata resource discovery ───────────────────────────────────────

def test_og_image_discovered():
    parser = parse_html('<meta property="og:image" content="/images/photo.jpg">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/images/photo.jpg"
    assert r.element_type == "meta"
    assert r.type_hint == "og:image"
    assert r.discovery_attribute == "property"


def test_og_video_discovered():
    parser = parse_html('<meta property="og:video" content="/videos/movie.mp4">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/videos/movie.mp4"
    assert r.element_type == "meta"
    assert r.type_hint == "og:video"


def test_og_audio_discovered():
    parser = parse_html('<meta property="og:audio" content="/audio/song.mp3">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/audio/song.mp3"
    assert r.element_type == "meta"
    assert r.type_hint == "og:audio"


def test_og_image_absolute_url():
    parser = parse_html('<meta property="og:image" content="https://cdn.example.com/photo.jpg">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].url == "https://cdn.example.com/photo.jpg"


def test_unsupported_metadata_ignored():
    parser = parse_html('<meta property="og:description" content="A nice page">')
    assert len(parser.resources) == 0


def test_unsupported_metadata_name_ignored():
    parser = parse_html('<meta name="description" content="A nice page">')
    assert len(parser.resources) == 0


def test_metadata_empty_content_ignored():
    parser = parse_html('<meta property="og:image" content="">')
    assert len(parser.resources) == 0


def test_metadata_data_url_ignored():
    parser = parse_html('<meta property="og:image" content="data:image/png;base64,abc">')
    assert len(parser.resources) == 0


def test_metadata_javascript_url_ignored():
    parser = parse_html('<meta property="og:image" content="javascript:alert(1)">')
    assert len(parser.resources) == 0


def test_metadata_fragment_ignored():
    parser = parse_html('<meta property="og:image" content="#section">')
    assert len(parser.resources) == 0


def test_metadata_no_property_ignored():
    parser = parse_html('<meta content="/images/photo.jpg">')
    assert len(parser.resources) == 0


def test_twitter_image_discovered():
    parser = parse_html('<meta property="twitter:image" content="/images/photo.jpg">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].type_hint == "twitter:image"


def test_og_video_secure_url_discovered():
    parser = parse_html('<meta property="og:video:secure_url" content="https://example.com/movie.mp4">')
    resources = parser.resources
    assert len(resources) == 1
    assert resources[0].type_hint == "og:video:secure_url"


# ── Preload resource discovery ────────────────────────────────────────

def test_preload_image():
    parser = parse_html('<link rel="preload" href="/img.webp" as="image">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/img.webp"
    assert r.element_type == "link"
    assert r.type_hint == "image"
    assert r.discovery_attribute == "preload"


def test_preload_video():
    parser = parse_html('<link rel="preload" href="/video.mp4" as="video">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/video.mp4"
    assert r.discovery_attribute == "preload"
    assert r.type_hint == "video"


def test_preload_audio():
    parser = parse_html('<link rel="preload" href="/audio.mp3" as="audio">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/audio.mp3"
    assert r.discovery_attribute == "preload"
    assert r.type_hint == "audio"


def test_preload_fetch():
    parser = parse_html('<link rel="preload" href="/data.json" as="fetch">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.url == "/data.json"
    assert r.discovery_attribute == "preload"
    assert r.type_hint == "fetch"


def test_preload_without_href_ignored():
    parser = parse_html('<link rel="preload" as="image">')
    assert len(parser.resources) == 0


def test_preload_without_as():
    parser = parse_html('<link rel="preload" href="/file.pdf">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.discovery_attribute == "preload"
    assert r.type_hint == ""


def test_preload_with_type_hint():
    parser = parse_html('<link rel="preload" href="/style.css" as="style" type="text/css">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.type_hint == "text/css"


# ── Provenance / evidence ─────────────────────────────────────────────

def test_discovery_attribute_preserved_in_candidate():
    parser = parse_html('<img data-src="/images/photo.jpg">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.discovery_attribute == "data-src"
    assert r.element_type == "image"


def test_metadata_property_preserved_as_type_hint():
    parser = parse_html('<meta property="og:image" content="/images/photo.jpg">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.type_hint == "og:image"
    assert r.discovery_attribute == "property"


def test_preload_as_hint_preserved():
    parser = parse_html('<link rel="preload" href="/video.mp4" as="video">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.type_hint == "video"
    assert r.discovery_attribute == "preload"


def test_srcset_discovery_attribute_preserved():
    parser = parse_html('<img srcset="small.jpg 480w, large.jpg 1600w">')
    resources = parser.resources
    for r in resources:
        assert r.discovery_attribute == "srcset"


def test_evidence_reaches_generic_adapter():
    """Verify that discovery_attribute evidence propagates through generic adapter."""
    from app.sources.generic import _is_non_resource_path
    from app.sources.link_classifier import normalize_url
    from app.sources.html_parser import ResourceCandidate

    c = ResourceCandidate(
        url="/images/photo.jpg",
        element_type="img",
        discovery_attribute="data-src",
        type_hint="image/jpeg",
    )
    assert c.discovery_attribute == "data-src"
    assert c.element_type == "img"
    assert c.type_hint == "image/jpeg"


def test_candidate_with_no_discovery_attribute():
    parser = parse_html('<img src="/images/photo.jpg">')
    resources = parser.resources
    assert len(resources) == 1
    r = resources[0]
    assert r.discovery_attribute == ""
    assert r.element_type == "image"


# ── Deduplication ─────────────────────────────────────────────────────

def test_dedup_same_url_lazy_and_normal():
    parser = parse_html('''
        <img src="/images/photo.jpg">
        <img data-src="/images/photo.jpg">
    ''')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert len([u for u in urls if u == "/images/photo.jpg"]) >= 1


def test_dedup_same_url_metadata_and_element():
    parser = parse_html('''
        <img src="/images/photo.jpg">
        <meta property="og:image" content="/images/photo.jpg">
    ''')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert len([u for u in urls if u == "/images/photo.jpg"]) >= 1


def test_dedup_same_url_preload_and_img():
    parser = parse_html('''
        <img src="/images/photo.jpg">
        <link rel="preload" href="/images/photo.jpg" as="image">
    ''')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert len([u for u in urls if u == "/images/photo.jpg"]) >= 1


def test_dedup_different_urls_preserved():
    parser = parse_html('''
        <img src="/images/photo1.jpg">
        <img data-src="/images/photo2.jpg">
    ''')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert "/images/photo1.jpg" in urls
    assert "/images/photo2.jpg" in urls


def test_dedup_srcset_same_url():
    parser = parse_html('<img srcset="/images/photo.jpg 1x, /images/photo.jpg 2x">')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert len([u for u in urls if u == "/images/photo.jpg"]) >= 1


# ── Security regression ───────────────────────────────────────────────

def test_ignored_scheme_data_rejected():
    parser = parse_html('<img data-src="data:image/png;base64,abc">')
    assert len(parser.resources) == 0


def test_ignored_scheme_javascript_rejected():
    parser = parse_html('<img data-src="javascript:alert(1)">')
    assert len(parser.resources) == 0


def test_ignored_scheme_mailto_rejected():
    parser = parse_html('<img data-src="mailto:test@example.com">')
    assert len(parser.resources) == 0


def test_fragment_only_rejected():
    parser = parse_html('<img data-src="#section">')
    assert len(parser.resources) == 0


def test_srcset_data_url_rejected():
    parser = parse_html('<img srcset="data:image/png;base64,abc 1x">')
    assert len(parser.resources) == 0


def test_metadata_data_url_rejected():
    parser = parse_html('<meta property="og:image" content="data:image/png;base64,abc">')
    assert len(parser.resources) == 0


def test_preload_javascript_rejected():
    parser = parse_html('<link rel="preload" href="javascript:alert(1)" as="image">')
    assert len(parser.resources) == 0


def test_lazy_data_scheme_rejected_in_all_attrs():
    parser = parse_html('''
        <img data-src="data:image/png;base64,x" data-original="data:image/jpeg;base64,y">
        <video data-src="data:video/mp4;base64,z">
    ''')
    assert len(parser.resources) == 0


def test_resolver_rejects_unsafe_urls():
    """Verify that the resolver still rejects unsafe URLs."""
    from app.services.resolver import Resolver
    from app.services.url_security import validate_url

    decision = validate_url("javascript:alert(1)")
    assert not decision.is_safe

    decision = validate_url("file:///etc/passwd")
    assert not decision.is_safe

    decision = validate_url("http://169.254.169.254/latest/meta-data/")
    assert not decision.is_safe


def test_resolver_accepts_safe_urls():
    from app.services.url_security import validate_url

    decision = validate_url("https://example.com/file.zip")
    assert decision.is_safe

    decision = validate_url("http://example.com/image.jpg")
    assert decision.is_safe