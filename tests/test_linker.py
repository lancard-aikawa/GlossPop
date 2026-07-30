from __future__ import annotations

from glosspop.linker import Linker
from glosspop.models import Entry


def mk(term: str, *, aliases: list[str] | None = None, slug: str | None = None) -> Entry:
    return Entry(term=term, aliases=aliases or [], slug=slug or term.lower(), category="テスト")


def link(entries, html, **kw):
    return Linker(entries).annotate(html, **kw)


def test_links_japanese_term_mid_sentence():
    out, hits = link([mk("機械学習", slug="ml")], "<p>今日は機械学習の話をします。</p>")
    assert '<a class="gloss-link" href="/glossary/ml"' in out
    assert ">機械学習</a>" in out
    assert [e.slug for e in hits] == ["ml"]


def test_does_not_touch_code_pre_or_existing_links():
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


def test_ascii_word_boundary():
    out, hits = link([mk("API", slug="api")], "<p>APIs と rapid と API</p>")
    assert out.count('class="gloss-link"') == 1
    assert "APIs" in out and "rapid" in out


def test_longest_match_wins():
    entries = [mk("学習", slug="gakushu"), mk("機械学習", slug="ml")]
    out, _ = link(entries, "<p>機械学習</p>")
    assert "/glossary/ml" in out
    assert "/glossary/gakushu" not in out


def test_alias_links_to_same_entry():
    entries = [mk("イミュータブル", aliases=["immutable", "不変オブジェクト"], slug="immutable")]
    out, hits = link(entries, "<p>immutable な値、つまり不変オブジェクト。</p>")
    assert out.count('href="/glossary/immutable"') == 2
    assert [e.slug for e in hits] == ["immutable"]


def test_case_insensitive_but_keeps_original_surface():
    out, _ = link([mk("Docker", slug="docker")], "<p>docker と DOCKER</p>")
    assert ">docker</a>" in out
    assert ">DOCKER</a>" in out


def test_first_only_links_once_per_term():
    entries = [mk("用語", slug="term")]
    out, _ = link(entries, "<p>用語 と 用語 と 用語</p>", first_only=True)
    assert out.count('class="gloss-link"') == 1


def test_skip_slugs_prevents_self_link():
    entries = [mk("再帰", slug="recursion")]
    out, hits = link(entries, "<p>再帰とは再帰である</p>", skip_slugs=["recursion"])
    assert 'class="gloss-link"' not in out
    assert hits == []


def test_matches_html_escaped_surface():
    entries = [mk("A&B", slug="ab")]
    out, _ = link(entries, "<p>A&amp;B 社の話</p>")
    assert 'href="/glossary/ab"' in out
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
