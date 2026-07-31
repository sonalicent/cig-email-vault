from email_pipeline.html_to_md import html_to_markdown


def test_converts_basic_html_to_markdown():
    html = "<html><body><p>Hello <b>team</b>,</p><p>See the link.</p></body></html>"
    md = html_to_markdown(html)
    assert "Hello" in md
    assert "**team**" in md
    assert "See the link." in md


def test_strips_scripts_and_styles():
    html = "<html><head><style>p{color:red}</style></head><body><p>Keep me</p>" \
           "<script>alert('x')</script></body></html>"
    md = html_to_markdown(html)
    assert "Keep me" in md
    assert "alert" not in md
    assert "color:red" not in md


def test_empty_input_returns_empty_string():
    assert html_to_markdown("") == ""
    assert html_to_markdown("   ") == ""


def test_collapses_excess_blank_lines():
    html = "<p>a</p><br><br><br><br><p>b</p>"
    md = html_to_markdown(html)
    assert "\n\n\n" not in md
