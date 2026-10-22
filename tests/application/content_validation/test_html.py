"""Limited HTML grammar, exact text derivation, and serialization."""

import pytest

from voice_archive_api.application.content_validation.html import parse_fragment


@pytest.mark.parametrize(
    ("html", "kind", "expected"),
    [
        ("<p>  a\n b\t c  </p>", "prose", "  a\n b\t c  "),
        ("<p>&lt;b&gt;&amp;&#x1F600;&#233;é</p>", "verse", "<b>&😀éé"),
        ("<p><strong>a<em>b</em>c</strong>d</p>", "prose", "abcd"),
        ("<p>a<br/>b<br />c<br>d</p>", "verse", "a\nb\nc\nd"),
        ("<h6 class='align-left gap-after'>H</h6>", "heading", "H"),
        ("<p class='no-indent gap-after align-center gap-before'>◆</p>", "prose", "◆"),
    ],
)
def test_text_derivation_and_stable_serialization(html, kind, expected):
    normalized, text = parse_fragment(html, kind)
    assert text == expected
    assert parse_fragment(normalized, kind) == (normalized, expected)


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<p></p>",
        "<p> \n<br>&nbsp;</p>",
        "text",
        " <p>x</p>",
        "<p>x</p> ",
        "<p>x</p><p>y</p>",
        "<p><p>x</p></p>",
        "<h1>x</h1>",
        "<div>x</div>",
        "<p><b>x</b></p>",
        "<p><i>x</i></p>",
        "<p><a href='x'>x</a></p>",
        "<p><img src='x'></p>",
        "<p><svg><text>x</text></svg></p>",
        "<p xmlns='evil'>x</p>",
        "<p><x:em>x</x:em></p>",
        "<p style='x'>x</p>",
        "<p onclick='x'>x</p>",
        "<p id='x'>x</p>",
        "<p><em class='no-indent'>x</em></p>",
        "<p><br class='gap-before'>x</p>",
        "<p><br>x</br></p>",
        "<p>x",
        "<p><em>x</p></em>",
        "<p><strong>x</p>",
        "<p/>x",
        "<p><em/>x</p>",
        "<p class='evil'>x</p>",
        "<p class=''>x</p>",
        "<p class='align-left align-right'>x</p>",
        "<p class='gap-before gap-before'>x</p>",
        "<p class='no-indent' class='gap-after'>x</p>",
        "<p>&madeup;</p>",
        "<p>&#0;</p>",
        "<p>&#13;</p>",
        "<p>&#x80;</p>",
        "<p>&#x110000;</p>",
        "<p>x\r\ny</p>",
        "<p>\x00x</p>",
        "<p>\ud800</p>",
        "<p><!--comment-->x</p>",
        "<!DOCTYPE html><p>x</p>",
        "<?xml x?><p>x</p>",
        "<p><![CDATA[x]]></p>",
        "<p>x</p bad>",
        "<p / >x</p>",
        "<p>x < y</p>",
        "<p class='no-indent>x</p>",
        "<p>&amp x</p>",
        "<p class='align-left\xa0no-indent'>x</p>",
    ],
)
def test_invalid_html_is_rejected(html):
    with pytest.raises(ValueError):
        parse_fragment(html, "prose")


@pytest.mark.parametrize(
    "html",
    [
        "<p>" + "<em>" * 33 + "x" + "</em>" * 33 + "</p>",
        "<p>" + "x" * 128001 + "</p>",
        "<p>" + "界" * 100000 + "</p>",
        "<p>" + "x<br>" * 4096 + "</p>",
    ],
    ids=["depth", "text", "bytes", "nodes"],
)
def test_html_resource_limits(html):
    with pytest.raises(ValueError):
        parse_fragment(html, "prose")


def test_line_breaks_count_toward_derived_text_limit():
    with pytest.raises(ValueError, match="Derived text exceeds"):
        parse_fragment("<p>" + "x" * 128000 + "<br></p>", "prose")


def test_serialized_html_limit_after_escaping():
    with pytest.raises(ValueError, match="Serialized HTML exceeds"):
        parse_fragment("<p>" + ">" * 100000 + "</p>", "prose")
