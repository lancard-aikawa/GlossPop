from __future__ import annotations

import pytest

from glosspop.htmlclean import clean_html

BASE = "https://example.com/docs/page.html"


def clean(html: str, base: str = BASE) -> str:
    return clean_html(html, base_url=base)[0]


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        "<style>body{display:none}</style>",
        "<iframe src='https://evil.example'></iframe>",
        "<noscript>fallback</noscript>",
        "<form><input name=x><button>go</button></form>",
        "<svg><circle r='1'/></svg>",
    ],
)
def test_dangerous_trees_are_dropped_with_their_content(html):
    out = clean(f"<p>keep</p>{html}")
    assert "keep" in out
    for word in ("alert", "display:none", "evil.example", "fallback", "go", "circle"):
        assert word not in out


def test_event_handlers_and_unknown_attributes_are_stripped():
    out = clean('<p onclick="evil()" class="x" style="color:red">本文</p>')
    assert out == "<p>本文</p>"


def test_javascript_url_is_dropped_but_text_survives():
    out = clean('<a href="javascript:alert(1)">押す</a>')
    assert "javascript" not in out
    assert "押す" in out


def test_relative_links_are_made_absolute():
    out = clean('<a href="../other.html">先</a><img src="/img/a.png" alt="図">')
    assert 'href="https://example.com/other.html"' in out
    assert 'src="https://example.com/img/a.png"' in out
    assert 'alt="図"' in out


def test_base_tag_is_honoured():
    out = clean('<base href="https://cdn.example.net/x/"><a href="y.html">先</a>')
    assert 'href="https://cdn.example.net/x/y.html"' in out


def test_in_page_anchor_is_dropped():
    out = clean('<a href="#sec">章へ</a>')
    assert "href" not in out
    assert "章へ" in out


def test_main_content_is_preferred_over_chrome():
    html = (
        "<body><nav>ナビ</nav><header>ヘッダ</header>"
        "<main><h1>見出し</h1><p>本文</p></main>"
        "<footer>フッタ</footer></body>"
    )
    out = clean(html)
    assert "見出し" in out and "本文" in out
    for chrome in ("ナビ", "ヘッダ", "フッタ"):
        assert chrome not in out


def test_article_is_used_when_there_is_no_main():
    out = clean("<body><aside>脇</aside><article><p>記事</p></article></body>")
    assert "記事" in out
    assert "脇" not in out


def test_multiple_articles_all_survive():
    """記事ごとに ``<article>`` が並ぶページで、前の記事が消えないこと。

    2 つめ以降でも本文を取り直すと確定済みの ``parts`` を上書きしてしまい、
    1 つめの記事が丸ごと落ちていた（``<input>`` の不具合と同じ「黙って本文が
    消える」系統）。
    """
    html = (
        "<body><nav>ナビ</nav>"
        "<article><h2>1つめ</h2><p>最初の記事</p></article>"
        "<article><h2>2つめ</h2><p>次の記事</p></article>"
        "<p>記事の外</p></body>"
    )
    out = clean(html)
    for kept in ("1つめ", "最初の記事", "2つめ", "次の記事", "記事の外"):
        assert kept in out
    assert "ナビ" not in out


def test_article_inside_main_does_not_restart_the_body():
    out = clean("<main><p>前置き</p><article><p>記事</p></article></main>")
    assert "前置き" in out and "記事" in out



def test_falls_back_to_whole_body():
    out = clean("<body><div><p>ふつうの本文</p></div></body>")
    assert "ふつうの本文" in out


def test_unknown_tags_keep_their_text():
    out = clean("<custom-tag>中身</custom-tag>")
    assert "中身" in out
    assert "custom-tag" not in out


def test_text_is_escaped():
    out = clean("<p>a &lt; b &amp; c</p>")
    assert "&lt;" in out and "&amp;" in out


def test_title_is_extracted():
    _, title = clean_html("<html><head><title> ページの題  </title></head><body><p>x</p></body></html>")
    assert title == "ページの題"


def test_unclosed_tags_do_not_leak():
    out = clean("<p>ひとつ<p>ふたつ")
    assert out.count("<p>") == 2
    assert out.endswith("</p>")


def test_is_idempotent():
    once = clean("<main><p>本文 <a href='/a'>リンク</a></p></main>")
    assert clean(once, "") == once


def test_void_elements_in_drop_trees_do_not_eat_the_rest():
    """``<input>`` は閉じタグを持たない。

    void 要素として扱わないと、中身ごと捨てる処理が閉じタグを待ち続け、
    それ以降の本文が全部消える（検索ボックスのあるページが空になっていた）。
    """
    out = clean('<p>前</p><form><input type="text"></form><p>後ろ</p>')
    assert "前" in out and "後ろ" in out
    assert "input" not in out


@pytest.mark.parametrize("void", ["input", "embed", "frame", "source", "track"])
def test_every_void_element_in_drop_trees_is_known(void):
    from glosspop.htmlclean import DROP_TREES, VOID_TAGS

    # DROP_TREES 側に void 要素を足すときは VOID_TAGS にも足すこと
    if void in DROP_TREES:
        assert void in VOID_TAGS


def test_page_with_a_search_box_keeps_its_body():
    html = (
        "<html><body><nav>ナビ</nav>"
        '<form class="search"><input name="q"><button>検索</button></form>'
        "<div><h1>見出し</h1><p>本文がここにある。</p></div>"
        "</body></html>"
    )
    out = clean(html)
    assert "本文がここにある。" in out
    assert "ナビ" not in out and "検索" not in out
