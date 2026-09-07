from app.sources.html_parser import parse_html


def test_extracts_title():
    html = "<html><head><title>Game Page</title></head><body></body></html>"
    parser = parse_html(html)
    assert parser.title == "Game Page"


def test_title_with_whitespace():
    html = "<html><head><title>   Spaced Title   </title></head></html>"
    parser = parse_html(html)
    assert parser.title == "Spaced Title"


def test_missing_title_returns_empty():
    html = "<html><body><h1>No title</h1></body></html>"
    parser = parse_html(html)
    assert parser.title == ""


def test_extracts_anchors_with_href():
    html = '<html><body><a href="https://example.com/file.zip">Download</a></body></html>'
    parser = parse_html(html)
    assert len(parser.links) == 1
    assert parser.links[0].href == "https://example.com/file.zip"
    assert parser.links[0].text == "Download"


def test_extracts_multiple_anchors():
    html = '''
    <html><body>
      <a href="https://a.com/x.zip">Part 1</a>
      <a href="/relative.zip">Part 2</a>
      <a>no href</a>
    </body></html>
    '''
    parser = parse_html(html)
    assert len(parser.links) == 2
    assert parser.links[0].href == "https://a.com/x.zip"
    assert parser.links[0].text == "Part 1"
    assert parser.links[1].href == "/relative.zip"
    assert parser.links[1].text == "Part 2"


def test_anchor_with_no_text():
    html = '<a href="https://example.com/file.zip"></a>'
    parser = parse_html(html)
    assert len(parser.links) == 1
    assert parser.links[0].text == ""


def test_malformed_html_does_not_raise():
    html = "<html><head><title>Test<"
    parser = parse_html(html)
    assert parser is not None


def test_html_with_no_body():
    html = ""
    parser = parse_html(html)
    assert parser.title == ""
    assert parser.links == []


def test_uppercase_tags():
    html = "<HTML><HEAD><TITLE>Upper</TITLE></HEAD></HTML>"
    parser = parse_html(html)
    assert parser.title == "Upper"


def test_links_inside_nested_elements():
    html = '<div><p><a href="https://example.com/x.zip">Nested Link</a></p></div>'
    parser = parse_html(html)
    assert len(parser.links) == 1
    assert parser.links[0].text == "Nested Link"