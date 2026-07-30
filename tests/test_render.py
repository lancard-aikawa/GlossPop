from __future__ import annotations

from glosspop.render import definition_to_html, md_to_html, soften_paragraphs

LONG = (
    "同じ操作を繰り返しても状態が変わらない性質を指す。"
    "HTTP では GET や PUT が該当する。"
    "リトライしても壊れないので、不安定な回線では前提として重要になる。"
)


def test_long_paragraph_is_split_per_sentence():
    out = soften_paragraphs(LONG)
    assert out.split("\n") == [
        "同じ操作を繰り返しても状態が変わらない性質を指す。",
        "HTTP では GET や PUT が該当する。",
        "リトライしても壊れないので、不安定な回線では前提として重要になる。",
    ]


def test_short_line_is_left_alone():
    text = "短い一文。もう一文。"
    assert soften_paragraphs(text) == text


def test_blank_lines_between_paragraphs_survive():
    out = soften_paragraphs(f"{LONG}\n\n{LONG}")
    assert "\n\n" in out
    assert out.count("\n\n") == 1


def test_is_idempotent():
    once = soften_paragraphs(LONG)
    assert soften_paragraphs(once) == once


def test_code_fence_is_untouched():
    text = "```python\n" + "x = 1  # 長い行。これも一文。さらに一文。もっと一文。ずっと一文。まだ一文。\n" + "```"
    assert soften_paragraphs(text) == text


def test_inline_code_period_is_not_a_break():
    code = "`a。b。c。d。e。f。g。h。i。j。k。l。m。n。o。p。q。r。s。t。u`"
    text = f"設定ファイルには {code} のように書くのが決まりになっている。理由は後方互換のためである。"
    out = soften_paragraphs(text)
    assert code in out
    assert out.split("\n") == [
        f"設定ファイルには {code} のように書くのが決まりになっている。",
        "理由は後方互換のためである。",
    ]


def test_closing_bracket_after_period_stays_together():
    text = (
        "これは注記(補足。)であり、そのあとに続く長い文がここに来ることになっている。"
        "さらにもう一文が続いて、行全体はしきい値を超える長さになる。"
    )
    out = soften_paragraphs(text)
    assert "(補足。)" in out
    assert out.split("\n") == [
        "これは注記(補足。)であり、そのあとに続く長い文がここに来ることになっている。",
        "さらにもう一文が続いて、行全体はしきい値を超える長さになる。",
    ]


def test_heading_and_table_are_untouched():
    heading = "# 見出し。とても長い見出し。まだ続く見出し。さらに続く見出し。もっと続く見出し。"
    table = "| 列。 | 値。 | 補足。 | さらに補足。 | もっと補足。 | ずっと補足。 | まだ補足。 |"
    assert soften_paragraphs(heading) == heading
    assert soften_paragraphs(table) == table


def test_list_item_continuation_is_indented_to_content():
    text = f"- {LONG}"
    lines = soften_paragraphs(text).split("\n")
    assert lines[0] == "- 同じ操作を繰り返しても状態が変わらない性質を指す。"
    assert lines[1] == "  HTTP では GET や PUT が該当する。"
    assert lines[2].startswith("  リトライしても")


def test_definition_html_turns_single_newlines_into_br():
    html = definition_to_html(LONG)
    assert html.count("<br>") == 2
    assert html.count("<p>") == 1


def test_source_documents_keep_standard_markdown_semantics():
    # ビューアで開く文書は CommonMark どおり: 単一改行は改行にならない
    assert "<br>" not in md_to_html("1 行目\n2 行目")
