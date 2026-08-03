from __future__ import annotations

from glosspop.linker import Linker
from glosspop.models import Entry


def mk(term: str, *, aliases: list[str] | None = None, category: str = "テスト", slug: str | None = None) -> Entry:
    return Entry(term=term, aliases=aliases or [], slug=slug or term.lower(), category=category)


def link(entries, html, **kw):
    return Linker(entries).annotate(html, **kw)


def test_links_japanese_term_mid_sentence():
    out, hits = link([mk("機械学習", slug="ml")], "<p>今日は機械学習の話をします。</p>")
    assert 'class="gloss-link"' in out
    assert 'href="/glossary/%E3%83%86%E3%82%B9%E3%83%88/ml"' in out
    assert ">機械学習</a>" in out
    assert [e.ref for e in hits] == ["テスト/ml"]


def test_does_not_touch_code_blocks_or_existing_links():
    entries = [mk("API", slug="api")]
    html = (
        "<p>API を叩く</p>"
        "<pre><code>call API here</code></pre>"
        "<p><a href='/x'>API ドキュメント</a></p>"
    )
    out, hits = link(entries, html)
    assert out.count('class="gloss-link"') == 1
    assert "<pre><code>call API here</code></pre>" in out
    assert ">API ドキュメント</a>" in out
    assert len(hits) == 1


def test_links_inside_inline_code():
    # 日本語文書では `用語` を強調のつもりで書くので、ここはリンクにする
    entries = [mk("機械学習", category="プログラミング", slug="機械学習")]
    out, hits = link(entries, "<p><code>機械学習</code> が登録されていれば</p>")
    assert '<code><a class="gloss-link"' in out
    assert ">機械学習</a></code>" in out
    assert len(hits) == 1


def test_inline_code_inside_a_code_block_is_still_skipped():
    entries = [mk("機械学習", category="プログラミング", slug="機械学習")]
    out, hits = link(entries, "<pre><code>model = 機械学習()</code></pre>")
    assert "gloss-link" not in out
    assert hits == []


def test_ascii_word_boundary():
    out, _ = link([mk("API", slug="api")], "<p>APIs と rapid と API</p>")
    assert out.count('class="gloss-link"') == 1
    assert "APIs" in out and "rapid" in out


def test_short_uppercase_alias_does_not_match_lowercase():
    """``MD`` が ``README.md`` の拡張子に当たらないこと。

    境界チェックは「英数字以外なら通す」ので ``.`` は境界として通る。短い略語を
    大文字小文字無視で照合していたため、README を開くと拡張子が軒並みリンクに
    なっていた（インラインコードを対象に加えて表面化した）。
    """
    entries = [mk("Markdown", aliases=["MD"], slug="markdown")]
    out, hits = link(entries, "<p>README.md と <code>docs/md/x</code> と MD 記法</p>")
    assert out.count('class="gloss-link"') == 1
    assert "README.md" in out and "docs/md/x" in out
    assert ">MD</a>" in out
    assert len(hits) == 1


def test_long_uppercase_term_is_still_case_insensitive():
    """区別するのは短い略語だけ。ふつうの語の照合規則は変えない。"""
    out, _ = link([mk("Markdown", slug="markdown")], "<p>markdown で書く</p>")
    assert ">markdown</a>" in out


def test_longest_match_wins():
    entries = [mk("学習", slug="gakushu"), mk("機械学習", slug="ml")]
    out, _ = link(entries, "<p>機械学習</p>")
    assert "/ml" in out
    assert "gakushu" not in out


def test_alias_links_to_same_entry():
    entries = [mk("イミュータブル", aliases=["immutable", "不変オブジェクト"], slug="immutable")]
    out, hits = link(entries, "<p>immutable な値、つまり不変オブジェクト。</p>")
    assert out.count('class="gloss-link"') == 2
    assert [e.ref for e in hits] == ["テスト/immutable"]


def test_case_insensitive_but_keeps_original_surface():
    out, _ = link([mk("Docker", slug="docker")], "<p>docker と DOCKER</p>")
    assert ">docker</a>" in out
    assert ">DOCKER</a>" in out


def test_first_only_links_once_per_surface():
    entries = [mk("用語", slug="term")]
    out, _ = link(entries, "<p>用語 と 用語 と 用語</p>", first_only=True)
    assert out.count('class="gloss-link"') == 1


def test_skip_refs_prevents_self_link():
    entries = [mk("再帰", slug="recursion")]
    out, hits = link(entries, "<p>再帰とは再帰である</p>", skip_refs=["テスト/recursion"])
    assert 'class="gloss-link"' not in out
    assert hits == []


def test_matches_html_escaped_surface():
    entries = [mk("A&B", slug="ab")]
    out, _ = link(entries, "<p>A&amp;B 社の話</p>")
    assert "/ab" in out
    assert ">A&amp;B</a>" in out


def test_does_not_corrupt_attributes():
    entries = [mk("title", slug="title")]
    html = '<p title="title attribute">no title here</p>'
    out, _ = link(entries, html)
    assert '<p title="title attribute">' in out
    assert out.count('class="gloss-link"') == 1


def test_empty_glossary_is_noop():
    html = "<p>なにもリンクされない</p>"
    out, hits = link([], html)
    assert out == html
    assert hits == []


def test_hits_are_in_first_appearance_order():
    entries = [mk("あ", slug="a"), mk("い", slug="i"), mk("う", slug="u")]
    _, hits = link(entries, "<p>う い あ</p>")
    assert [e.slug for e in hits] == ["u", "i", "a"]


class TestAcrossInlineMarkup:
    """強調などで語が分断されていても、またいで一致させる。"""

    entries = [mk("冪等", category="プログラミング", slug="冪等")]

    def link(self, html, **kw):
        return link(self.entries, html, **kw)

    def test_whole_term_emphasised(self):
        out, hits = self.link("<p>この操作は<strong>冪等</strong>です。</p>")
        assert "<strong><a " in out
        assert len(hits) == 1

    def test_emphasis_splitting_the_term(self):
        out, hits = self.link("<p>この操作は<strong>冪</strong>等です。</p>")
        assert out.count('class="gloss-link gloss-split"') == 2
        assert ">冪</a></strong>" in out
        assert ">等</a>" in out
        assert len(hits) == 1

    def test_trailing_half_emphasised(self):
        out, hits = self.link("<p>結果は冪<em>等</em>だ。</p>")
        assert out.count("gloss-split") == 2
        assert len(hits) == 1

    def test_three_way_split(self):
        out, _ = self.link("<p><b>冪</b><i>等</i></p>")
        assert out.count('class="gloss-link gloss-split"') == 2


class TestRunBoundaries:
    """またいではいけない境界。"""

    entries = [mk("冪等", category="プログラミング", slug="冪等")]

    def link(self, html, **kw):
        return link(self.entries, html, **kw)

    def test_does_not_span_paragraphs(self):
        out, hits = self.link("<p>冪</p><p>等</p>")
        assert 'class="gloss-link"' not in out
        assert hits == []

    def test_does_not_span_list_items(self):
        out, hits = self.link("<ul><li>冪</li><li>等</li></ul>")
        assert "gloss-link" not in out
        assert hits == []

    def test_does_not_span_line_breaks(self):
        out, hits = self.link("<p>冪<br>等</p>")
        assert "gloss-link" not in out
        assert hits == []

    def test_does_not_span_table_cells(self):
        out, _ = self.link("<table><tr><td>冪</td><td>等</td></tr></table>")
        assert "gloss-link" not in out

    def test_intervening_inline_code_prevents_a_match(self):
        # 見えるテキストが「冪x等」になるのでマッチしない
        out, hits = self.link("<p>冪<code>x</code>等</p>")
        assert "gloss-link" not in out
        assert hits == []

    def test_spans_an_inline_code_boundary(self):
        # 見えるテキストは「冪等」なので、またいで一致する
        out, hits = self.link("<p>冪<code>等</code></p>")
        assert out.count("gloss-split") == 2
        assert len(hits) == 1

    def test_does_not_span_a_code_block(self):
        out, hits = self.link("<p>冪</p><pre><code>等</code></pre>")
        assert "gloss-link" not in out
        assert hits == []

    def test_intervening_text_prevents_a_match(self):
        out, _ = self.link("<p>冪<strong>の</strong>等</p>")
        assert "gloss-link" not in out

    def test_empty_inline_element_is_transparent(self):
        out, hits = self.link("<p>冪<strong></strong>等</p>")
        assert "gloss-split" in out
        assert len(hits) == 1


class TestDuplicateTerms:
    entries = [
        mk("ソース", category="プログラミング", slug="ソース"),
        mk("ソース", category="料理", slug="ソース"),
    ]

    def test_single_link_carries_the_surface_and_count(self):
        out, hits = link(self.entries, "<p>ソースを見る</p>")
        assert out.count('class="gloss-link"') == 1
        assert 'data-gloss="ソース"' in out
        assert 'data-count="2"' in out
        assert {e.ref for e in hits} == {"プログラミング/ソース", "料理/ソース"}

    def test_multiple_entries_link_to_search_not_a_single_page(self):
        out, _ = link(self.entries, "<p>ソース</p>")
        assert "/glossary?q=" in out

    def test_single_entry_links_straight_to_its_page(self):
        out, _ = link([self.entries[0]], "<p>ソース</p>")
        assert "/glossary?q=" not in out
        assert "/glossary/%E3%83%97%E3%83%AD%E3%82%B0%E3%83%A9%E3%83%9F%E3%83%B3%E3%82%B0/" in out
        assert 'data-count="1"' in out

    def test_skipping_one_falls_back_to_the_other(self):
        out, hits = link(self.entries, "<p>ソース</p>", skip_refs=["料理/ソース"])
        assert 'data-count="1"' in out
        assert [e.ref for e in hits] == ["プログラミング/ソース"]

    def test_skipping_all_leaves_plain_text(self):
        out, hits = link(self.entries, "<p>ソース</p>", skip_refs=["料理/ソース", "プログラミング/ソース"])
        assert 'class="gloss-link"' not in out
        assert hits == []


def test_term_split_by_ruby_still_links():
    """青空文庫 / epub のルビ。<ruby><rb>銀河</rb></ruby>鉄道 で 1 語として繋がること。"""
    from glosspop.linker import Linker
    from glosspop.models import Entry

    linker = Linker([Entry(term="銀河鉄道", category="作品", slug="銀河鉄道")])
    html, hits = linker.annotate("<p><ruby><rb>銀河</rb></ruby>鉄道に乗った。</p>")
    assert len(hits) == 1
    assert 'class="gloss-link' in html
