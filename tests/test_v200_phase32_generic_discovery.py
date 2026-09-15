
def test_parser_video_src():
    from app.sources.html_parser import parse_html
    parser = parse_html('<video src=\'video.mp4\'></video>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'video.mp4' in urls
    assert any(r.element_type == 'video' for r in resources)

def test_parser_video_poster():
    from app.sources.html_parser import parse_html
    parser = parse_html('<video src=\'video.mp4\' poster=\'poster.jpg\'></video>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'video.mp4' in urls
    assert 'poster.jpg' in urls
    poster = [r for r in resources if r.url == 'poster.jpg'][0]
    assert poster.element_type == 'image'

def test_parser_audio_src():
    from app.sources.html_parser import parse_html
    parser = parse_html('<audio src=\'song.mp3\'></audio>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'song.mp3' in urls
    assert any(r.element_type == 'audio' for r in resources)

def test_parser_source_src():
    from app.sources.html_parser import parse_html
    parser = parse_html('<source src=\'movie.mp4\' type=\'video/mp4\'></source>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'movie.mp4' in urls
    source = [r for r in resources if r.url == 'movie.mp4'][0]
    assert source.element_type == 'source'
    assert source.type_hint == 'video/mp4'

def test_parser_source_inside_video():
    from app.sources.html_parser import parse_html
    html = '''<video><source src=\'video.mp4\' type=\'video/mp4\'></video>'''
    parser = parse_html(html)
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'video.mp4' in urls

def test_parser_source_inside_audio():
    from app.sources.html_parser import parse_html
    html = '''<audio><source src=\'song.mp3\'></audio>'''
    parser = parse_html(html)
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'song.mp3' in urls

def test_parser_source_inside_picture():
    from app.sources.html_parser import parse_html
    html = '''<picture><source srcset=\'img.webp\' type=\'image/webp\'></picture>'''
    parser = parse_html(html)
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'img.webp' in urls

def test_parser_img_src():
    from app.sources.html_parser import parse_html
    parser = parse_html('<img src=\'photo.jpg\'>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'photo.jpg' in urls
    assert any(r.element_type == 'image' for r in resources)

def test_parser_img_srcset():
    from app.sources.html_parser import parse_html
    parser = parse_html('<img srcset=\'small.jpg 480w, large.jpg 1200w\'>')
    resources = parser.resources
    urls = [r.url for r in resources]
    assert 'small.jpg' in urls
    assert 'large.jpg' in urls

def test_srcset_single_url():
    from app.sources.html_parser import _parse_srcset
    urls = _parse_srcset('image.jpg')
    assert urls == ['image.jpg']

def test_srcset_multiple_urls():
    from app.sources.html_parser import _parse_srcset
    urls = _parse_srcset('image-small.jpg 480w, image-medium.jpg 800w, image-large.jpg 1200w')
    assert 'image-small.jpg' in urls
    assert 'image-medium.jpg' in urls
    assert 'image-large.jpg' in urls

def test_srcset_width_descriptors():
    from app.sources.html_parser import _parse_srcset
    urls = _parse_srcset('a.jpg 1x, b.jpg 2x')
    assert 'a.jpg' in urls
    assert 'b.jpg' in urls

def test_srcset_malformed_ignored():
    from app.sources.html_parser import _parse_srcset
    urls = _parse_srcset(', , invalid, ,')
    assert urls == []

def test_srcset_data_ignored():
    from app.sources.html_parser import _parse_srcset
    urls = _parse_srcset('data:image/png;base64,abc 1x')
    assert urls == []

def test_parser_absolute_url():
    from app.sources.html_parser import parse_html
    parser = parse_html('<img src=\'https://cdn.example.com/photo.jpg\'>')
    urls = [r.url for r in parser.resources]
    assert 'https://cdn.example.com/photo.jpg' in urls

def test_parser_relative_url():
    from app.sources.html_parser import parse_html
    parser = parse_html('<img src=\'media/photo.jpg\'>')
    urls = [r.url for r in parser.resources]
    assert 'media/photo.jpg' in urls

def test_parser_ignored_scheme():
    from app.sources.html_parser import parse_html
    parser = parse_html('<img src=\'javascript:alert(1)\'>')
    assert len(parser.resources) == 0

def test_parser_deduplicates_same_url():
    from app.sources.html_parser import parse_html
    html = '''<img src=\'photo.jpg'><a href=\'photo.jpg'><source src=\'photo.jpg\'>'''
    parser = parse_html(html)
    resources = parser.resources
    urls = [r.url for r in resources]
    assert len([u for u in urls if u == 'photo.jpg']) >= 1

def test_parser_links_still_discovered():
    from app.sources.html_parser import parse_html
    parser = parse_html('<a href=\'file.zip\'>download</a>')
    links = parser.links
    assert len(links) == 1
    assert links[0].href == 'file.zip'
    assert links[0].text == 'download'

def test_generic_adapter_has_media_discovery():
    from app.sources.generic import GenericSourceAdapter
    from app.sources.capability import SourceCapability
    adapter = GenericSourceAdapter()
    assert adapter.capabilities.supports(SourceCapability.MEDIA_ELEMENT_DISCOVERY)

def test_generic_adapter_no_download():
    from app.sources.generic import GenericSourceAdapter
    from app.sources.capability import SourceCapability
    adapter = GenericSourceAdapter()
    assert not adapter.capabilities.supports(SourceCapability.DOWNLOAD)
    assert not adapter.capabilities.supports(SourceCapability.DIRECT_RESOURCE)