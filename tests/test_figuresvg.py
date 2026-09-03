"""図の SVG を削る (`core.figuresvg`)。

ここへ来るのは **AI が書いた SVG** で、削り終えたものは**そのままブラウザの DOM に
入って PNG に焼かれる**。だから見張るのは 2 方向:

- **通してはいけないものが残っていないか**（スクリプト・外部参照・DTD）。
  許可制なので、抜けるとしたら「通した属性の**中身**」の側
- **通してよいものを落としていないか**。削りすぎて図形が消えると、呼ぶ側からは
  「AI が描けなかった」と区別が付かない

**描けなかったことは失敗ではない**（`why` を入れて空を返す）ので、例外は使わない。
"""

from __future__ import annotations

import pytest

from glosspop.core import figuresvg

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600">'


def wrap(body: str, head: str = HEAD) -> str:
    return f"{head}{body}</svg>"


class TestTheShapeItKeeps:
    def test_shapes_and_text_survive(self):
        got = figuresvg.clean(wrap(
            '<rect x="10" y="20" width="100" height="50" fill="#eee"/>'
            '<path d="M 0 0 L 10 10" stroke="#333" stroke-width="2"/>'
            '<text x="5" y="8" font-size="14">本丸</text>'
        ))
        assert got.why == ""
        assert got.shapes == 2
        assert got.texts == 1
        assert "本丸" in got.svg
        assert 'd="M 0 0 L 10 10"' in got.svg

    def test_box_comes_from_viewbox(self):
        got = figuresvg.clean(wrap('<rect width="8" height="6"/>',
                                   '<svg viewBox="-10 -5 800 600">'))
        assert got.box == (-10.0, -5.0, 800.0, 600.0)

    def test_size_is_rewritten_from_the_box(self):
        """**`viewBox` が正。** 食い違った `width` を残すと、焼いた PNG だけ歪む。"""
        got = figuresvg.clean(wrap(
            '<rect width="8" height="6"/>',
            '<svg viewBox="0 0 800 600" width="99" height="11">',
        ))
        assert 'width="800"' in got.svg
        assert 'height="600"' in got.svg

    def test_namespace_is_not_required(self):
        """`xmlns` を書き忘れた SVG も通す（AI は普通に書き忘れる）。"""
        got = figuresvg.clean(wrap('<rect width="8" height="6"/>',
                                   '<svg viewBox="0 0 8 6">'))
        assert got.why == ""
        assert 'xmlns="http://www.w3.org/2000/svg"' in got.svg

    def test_it_is_pulled_out_of_surrounding_prose(self):
        """前後に説明やコードフェンスが付いていても取り出す。"""
        got = figuresvg.clean(
            "はい、描きました。\n```xml\n"
            + wrap('<circle cx="1" cy="2" r="3"/>')
            + "\n```\nご確認ください。"
        )
        assert got.shapes == 1


class TestWhatItRefuses:
    def test_script_goes_with_its_contents(self):
        """タグだけ落として子を引き上げない —— 中身が図の文字として残る。"""
        got = figuresvg.clean(wrap(
            '<rect width="8" height="6"/><script>alert("x")</script>'
        ))
        assert "alert" not in got.svg
        assert "script" not in got.svg
        assert "script" in got.dropped

    @pytest.mark.parametrize("tag", ["image", "use", "foreignObject", "defs", "a"])
    def test_reference_and_container_tags(self, tag):
        got = figuresvg.clean(wrap(f'<rect width="8" height="6"/><{tag}/>'))
        assert f"<{tag}" not in got.svg

    def test_event_handlers_and_style(self):
        got = figuresvg.clean(wrap(
            '<rect width="8" height="6" onclick="x()" style="fill:red" class="a" id="b"/>'
        ))
        for gone in ("onclick", "style", "class", 'id="b"'):
            assert gone not in got.svg
        assert got.shapes == 1        # 属性を落としても図形は残す

    def test_xlink_href(self):
        got = figuresvg.clean(
            '<svg xmlns="http://www.w3.org/2000/svg"'
            ' xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 8 6">'
            '<rect width="8" height="6" xlink:href="http://example.com/x.png"/></svg>'
        )
        assert "example.com" not in got.svg

    @pytest.mark.parametrize("value", [
        "url(#a)", "url(http://example.com/x.png)", "javascript:x", "data:image/png;base64,AA",
    ])
    def test_bad_values_lose_the_attribute(self, value):
        """**許可制をすり抜けられるのは、通した属性の中身だけ。**"""
        got = figuresvg.clean(wrap(f'<rect width="8" height="6" fill="{value}"/>'))
        assert value not in got.svg
        assert got.shapes == 1

    def test_doctype_is_refused_before_parsing(self):
        """**実体の展開で膨らませる攻撃を、パーサへ渡す前に断る。**

        `extract()` は `<svg` より前を捨てるので、削ったあとで探すと宣言だけが
        視界から消える。だから見るのは**取り出す前の文字列**。
        """
        got = figuresvg.clean(
            '<!DOCTYPE svg [<!ENTITY a "xxxx">]>' + wrap('<rect width="8" height="6"/>')
        )
        assert got.svg == ""
        assert "DOCTYPE" in got.why

    def test_too_long(self):
        got = figuresvg.clean(wrap('<rect width="8" height="6"/>'
                                   + "<!-- " + "x" * figuresvg.MAX_CHARS + " -->"))
        assert got.svg == ""
        assert "大きすぎ" in got.why


class TestWhenItCannotBeUsed:
    """**描けなければ空。** 呼ぶ側は「作れませんでした」と出すだけで済む。"""

    def test_not_an_svg(self):
        got = figuresvg.clean("すみません、図にはできませんでした。")
        assert got.svg == ""
        assert got.why

    def test_broken_xml(self):
        got = figuresvg.clean(HEAD + '<rect width="8"')
        assert got.svg == ""

    def test_missing_viewbox(self):
        """寸法が無いと縦横比が決まらない（焼く側は box を必要とする）。"""
        got = figuresvg.clean(wrap('<rect width="8" height="6"/>',
                                   '<svg xmlns="http://www.w3.org/2000/svg">'))
        assert got.svg == ""
        assert "viewBox" in got.why

    @pytest.mark.parametrize("box", ["0 0 0 600", "0 0 800 -1", "abc"])
    def test_unusable_viewbox(self, box):
        got = figuresvg.clean(wrap('<rect width="8" height="6"/>',
                                   f'<svg viewBox="{box}">'))
        assert got.svg == ""

    def test_text_only_is_not_a_figure(self):
        """頼んだのは**図形と文字**であって、文字だけではない。"""
        got = figuresvg.clean(wrap('<text x="1" y="2">城</text>'))
        assert got.svg == ""
        assert "図形" in got.why


class TestWhatItReports:
    def test_dropped_names_are_unique_and_ordered(self):
        """同じものが何度落ちても**種類で返す**（凡例に出すため）。"""
        got = figuresvg.clean(wrap(
            '<rect width="8" height="6" onclick="a"/>'
            '<rect width="8" height="6" onclick="b"/>'
            '<script/>'
        ))
        assert got.dropped == ("rect@onclick", "script")
