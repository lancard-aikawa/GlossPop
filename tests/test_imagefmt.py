"""画像の**大きさ**を読む (`core.imagefmt.size`)。

見分け方 (`sniff`) と同じところに置いてあるのは、**片方だけ通ると気付けない**から
—— 受け入れた絵の大きさが読めないと、地図の点検が y の上限を黙って見なくなる。

**読めないことは異常ではない。** 途中で切れたバイト列も、まだ知らない形式もここへ
来る。呼ぶ側（点検）は「分からないなら見ない」に倒すので、**推測して返さない**
ことのほうが大事。
"""

from __future__ import annotations

import pytest

from glosspop.core import imagefmt


def png(width: int, height: int) -> bytes:
    """最小の PNG の頭。**IHDR は必ず先頭にある**ので、そこだけで足りる。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big") + b"IHDR"
        + width.to_bytes(4, "big") + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )


def gif(width: int, height: int) -> bytes:
    return b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00" * 3


def jpeg(width: int, height: int, *, before: bytes = b"") -> bytes:
    """SOF0 を 1 つ持つ JPEG の頭。``before`` で前に別の区切りを挟める。"""
    body = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03"
    return (
        b"\xff\xd8\xff" + before
        + b"\xff\xc0" + (len(body) + 2).to_bytes(2, "big") + body
    )


def app0(payload: bytes = b"JFIF\x00") -> bytes:
    return b"\xff\xe0" + (len(payload) + 2).to_bytes(2, "big") + payload


def webp_vp8x(width: int, height: int) -> bytes:
    return (
        b"RIFF" + (0).to_bytes(4, "little") + b"WEBP"
        + b"VP8X" + (10).to_bytes(4, "little") + b"\x00" * 4
        + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    )


def webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    return (
        b"RIFF" + (0).to_bytes(4, "little") + b"WEBP"
        + b"VP8L" + (5).to_bytes(4, "little") + b"\x2f" + bits.to_bytes(4, "little")
    )


def webp_vp8(width: int, height: int) -> bytes:
    return (
        b"RIFF" + (0).to_bytes(4, "little") + b"WEBP"
        + b"VP8 " + (20).to_bytes(4, "little") + b"\x00" * 3
        + b"\x9d\x01\x2a"
        + width.to_bytes(2, "little") + height.to_bytes(2, "little")
    )


class TestRasterFormats:
    """**受け入れる形式は全部読めること。** ここが欠けると、その形式の絵を
    使っている辞書でだけ点検が静かに緩む（画面には何も出ない）。
    """

    def test_png(self):
        assert imagefmt.size(png(2048, 1152)) == (2048, 1152)

    def test_gif(self):
        assert imagefmt.size(gif(640, 480)) == (640, 480)

    def test_jpeg(self):
        assert imagefmt.size(jpeg(1600, 900)) == (1600, 900)

    def test_jpeg_after_other_segments(self):
        """**SOF は先頭とは限らない。** JFIF や EXIF の後ろにあるのが普通。"""
        assert imagefmt.size(jpeg(1600, 900, before=app0(b"JFIF\x00" + b"\x00" * 40))) \
            == (1600, 900)

    @pytest.mark.parametrize("build", [webp_vp8x, webp_vp8l, webp_vp8])
    def test_webp_in_all_three_shapes(self, build):
        """WebP は中身が 3 通り（拡張・可逆・非可逆）で、書き方がどれも違う。"""
        assert imagefmt.size(build(1200, 800)) == (1200, 800)

    def test_every_accepted_suffix_has_a_reader(self):
        """**受け入れる拡張子と読める形式を揃える**（片方だけ増やさない）。"""
        samples = {
            ".png": png(10, 5), ".gif": gif(10, 5), ".jpg": jpeg(10, 5),
            ".jpeg": jpeg(10, 5), ".webp": webp_vp8x(10, 5),
            ".svg": b'<svg viewBox="0 0 10 5"></svg>',
        }
        assert set(samples) == set(imagefmt.MAP_SUFFIXES)
        for suffix, data in samples.items():
            assert imagefmt.size(data) == (10, 5), suffix


class TestSvg:
    """SVG は**書き方が 2 通り**（`width`/`height` と `viewBox`）。"""

    def test_width_and_height(self):
        assert imagefmt.size(b'<svg width="800" height="600"></svg>') == (800, 600)

    def test_units_in_px_are_fine(self):
        assert imagefmt.size(b"<svg width='800px' height='600px'></svg>") == (800, 600)

    def test_a_viewbox_is_used_when_there_is_no_size(self):
        assert imagefmt.size(b'<svg viewBox="0 0 200 100"></svg>') == (200, 100)

    def test_a_viewbox_may_be_comma_separated(self):
        assert imagefmt.size(b'<svg viewBox="0,0,200,100"></svg>') == (200, 100)

    def test_the_size_wins_over_the_viewbox(self):
        """**外の大きさが正**（`preserveAspectRatio` の既定は内側に収める）。"""
        got = imagefmt.size(b'<svg width="400" height="400" viewBox="0 0 200 100"></svg>')
        assert got == (400, 400)

    def test_a_percentage_falls_back_to_the_viewbox(self):
        """`%` は絵の中の比を決めない —— **あるほうを使う**。"""
        got = imagefmt.size(b'<svg width="100%" height="100%" viewBox="0 0 200 100"></svg>')
        assert got == (200, 100)

    def test_an_svg_with_no_size_at_all(self):
        assert imagefmt.size(b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>") is None


class TestUnreadable:
    """**読めなければ None。例外にしない。**"""

    @pytest.mark.parametrize("data", [
        b"",
        "# ただのテキスト".encode(),
        b"\x89PNG\r\n\x1a\n",                       # 途中で切れている
        b"\xff\xd8\xff",                            # JPEG の頭だけ
        b"RIFF\x00\x00\x00\x00WEBP",                # WebP の頭だけ
        b"GIF89a",
    ])
    def test_nothing_is_guessed(self, data):
        assert imagefmt.size(data) is None

    def test_zero_is_not_a_size(self):
        """0 を返すと、呼ぶ側が縦横比を出すところで割れる。"""
        assert imagefmt.size(png(0, 100)) is None
        assert imagefmt.size(b'<svg width="0" height="10"></svg>') is None

    def test_only_the_head_is_read(self):
        """**先頭だけ見る**（`sniff()` と同じ理由）。奥の `<svg` は拾わない。"""
        assert imagefmt.size(b"x" * 8192 + b'<svg width="10" height="5"></svg>') is None
