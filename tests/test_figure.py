"""図の下書き（`ai.draft_figure` と `/api/ai/figure`）の検算。

削る側は `test_figuresvg.py` が見ている。ここで見張るのは**頼み方と返し方**:

- **枠を宣言してから描かせる** —— 「この語の図を」とだけ頼むと AI は描きやすい
  ものへ寄る（`EXTRACT_KINDS` で登場人物が丸ごと落ちたのと同じ形）
- **描けなかったことを失敗にしない** —— 「描けなければ描かない」と頼んである
  ので、そう返ってきたぶんは正常な答え。502 にすると画面の文言が嘘になる
- **保存しない** —— 返すだけ。入れるかどうかは人が見てから決める
- **文体を渡さない** —— 効かせるのは人が読む文章だけ、という線をそのまま延ばす
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosspop import ai, store
from glosspop import llm
from glosspop.app import app

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600">'
    '<rect width="800" height="600" fill="#efe"/>'
    '<path d="M 10 10 L 700 500" stroke="#333"/>'
    '<text x="20" y="40">本丸</text>'
    "</svg>"
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def entry(add_entry):
    """材料を持った語を 1 つ置く（本文も要約もサーバが辞書から引く）。"""
    return add_entry(
        "桶狭間",
        category="地",
        summary="尾張の谷あい。",
        definition="南に入り江、北に尾根がある。中央の窪地で本陣が休んだ。",
    )


def answer(monkeypatch, text: str) -> dict:
    """AI の返事を差し替える。**`_generate` の 1 か所だけ**（提供元を知らない）。"""
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "_generate", lambda prompt, **_: text)
    return {}


# --------------------------------------------------------------------------- #
# 枠
# --------------------------------------------------------------------------- #


class TestTheFrames:
    def test_kinds_are_listed_for_the_screen(self, client):
        got = client.get("/api/ai/figure-kinds").json()
        assert [k["key"] for k in got["kinds"]] == list(ai.FIGURE_KINDS)
        assert got["default"] == ai.DEFAULT_FIGURE_KIND
        # プロンプト用の ** は落として渡す（画面にそのまま出る）
        assert all("**" not in k["hint"] for k in got["kinds"])

    def test_unknown_kind_falls_back(self):
        """覚えている値が読めないときと同じ扱い（黙って既定に落ちる）。"""
        assert ai.normalize_figure_kind("宇宙") == ai.DEFAULT_FIGURE_KIND
        assert ai.normalize_figure_kind("flow") == "flow"

    def test_the_frame_is_declared_in_the_prompt(self):
        prompt = ai.build_figure_prompt("桶狭間", kind="flow")
        assert ai.FIGURE_KINDS["flow"]["label"] in prompt
        assert "配置・地形" not in prompt      # 選ばれていない枠は載せない

    def test_no_relation_frame(self):
        """**関係・時刻・座標の図はここに足さない** —— 既存の 7 種類と重なる。"""
        joined = " ".join(s["label"] for s in ai.FIGURE_KINDS.values())
        for gone in ("関係", "相関", "時系列", "年表"):
            assert gone not in joined


# --------------------------------------------------------------------------- #
# 頼み方
# --------------------------------------------------------------------------- #


class TestHowItAsks:
    def test_the_material_comes_from_the_dictionary(self):
        prompt = ai.build_figure_prompt(
            "桶狭間", summary="尾張の谷あい。", definition="南に入り江。", category="地"
        )
        assert "尾張の谷あい。" in prompt
        assert "南に入り江。" in prompt
        assert "地" in prompt

    def test_the_note_is_capped(self):
        """長い指示は枠の宣言より目立ち、出力形式の指示を押しのける。"""
        prompt = ai.build_figure_prompt("桶狭間", note="あ" * (ai.FIGURE_NOTE_MAX + 50))
        assert "あ" * ai.FIGURE_NOTE_MAX in prompt
        assert "あ" * (ai.FIGURE_NOTE_MAX + 1) not in prompt

    def test_style_is_not_passed(self, monkeypatch):
        """**文体を渡さない。** 混ぜると絵の中の名前が本文と違うものになり、
        しかもそれは機械で気付けない（読みや時刻のようには検算できない）。"""
        monkeypatch.setattr(ai, "style", lambda: "軍記物のように勇壮に")
        prompt = ai.build_figure_prompt("桶狭間")
        assert "勇壮" not in prompt

    def test_it_is_told_it_may_refuse(self):
        """**枠を埋めるために描かせない。** 用意しないと本文に無いものを描く。"""
        prompt = ai.build_figure_prompt("桶狭間")
        assert ai.FIGURE_NONE in prompt
        assert "外れた図" in prompt

    def test_the_canvas_is_fixed_here(self):
        """大きさを AI に決めさせない（縦横比が毎回変わると並べたときに揃わない）。"""
        prompt = ai.build_figure_prompt("桶狭間")
        assert f"0 0 {ai.FIGURE_W} {ai.FIGURE_H}" in prompt


# --------------------------------------------------------------------------- #
# 返し方
# --------------------------------------------------------------------------- #


class TestWhatComesBack:
    def test_a_drawing_comes_back_cleaned(self, client, entry, monkeypatch):
        answer(monkeypatch, SVG)
        got = client.post(
            "/api/ai/figure", json={"ref": entry.ref, "kind": "layout"}
        ).json()
        assert got["svg"].startswith("<svg")
        assert got["box"] == [0, 0, 800, 600]
        assert got["shapes"] == 2 and got["texts"] == 1
        assert got["label"] == ai.FIGURE_KINDS["layout"]["label"]

    def test_refusing_is_not_a_failure(self, client, entry, monkeypatch):
        """「描けない」は AI が返してよい正常な答え。**502 にしない。**"""
        answer(monkeypatch, ai.FIGURE_NONE)
        res = client.post("/api/ai/figure", json={"ref": entry.ref})
        assert res.status_code == 200
        got = res.json()
        assert got["svg"] == ""
        assert "描けない" in got["why"]

    def test_refusal_is_not_confused_with_a_parse_failure(self, monkeypatch):
        """混ぜると「描けないと答えた」が「読めなかった」に化ける。"""
        assert "描けない" in ai.filter_figure("なし。").why
        assert "読めません" in ai.filter_figure('<svg viewBox="0 0 8 6"><rect></svg>').why

    def test_dropped_things_are_reported(self, client, entry, monkeypatch):
        """**黙って削った絵を出さない**（`hidden` / `outside` と同じ約束）。"""
        answer(monkeypatch, SVG.replace("<text", '<script>x</script><text'))
        got = client.post("/api/ai/figure", json={"ref": entry.ref}).json()
        assert "script" in got["dropped"]
        assert got["svg"]                      # 落としても図そのものは出す

    def test_nothing_is_saved(self, client, entry, monkeypatch):
        """**保存しない。** 入れるかどうかは人が見てから決める。"""
        answer(monkeypatch, SVG)
        client.post("/api/ai/figure", json={"ref": entry.ref})
        assert store.image_file(entry.ref) is None

    def test_unknown_entry(self, client, monkeypatch):
        answer(monkeypatch, SVG)
        assert client.post("/api/ai/figure", json={"ref": "地/無い"}).status_code == 404

    def test_without_ai(self, client, entry, monkeypatch):
        monkeypatch.setattr(ai, "available", lambda: False)
        assert client.post("/api/ai/figure", json={"ref": entry.ref}).status_code == 503


# --------------------------------------------------------------------------- #
# 焼いた先
# --------------------------------------------------------------------------- #


class TestWhereItLands:
    def test_the_image_slot_takes_png_only(self, client, entry):
        """**PNG で置く。** SVG は書き込み先すら返らない（1 鍵 1 枚の置き場所）。

        ブラウザが焼いた PNG がそのまま入ることを、口の側から確かめる
        （焼くのは `graph-export.js` なのでここでは作れない）。
        """
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        res = client.post(
            "/api/entry-image", params={"ref": entry.ref}, content=png,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert res.status_code == 200
        assert store.image_file(entry.ref).suffix == ".png"
        assert store.image_path(entry.ref, ".svg") is None

    def test_every_provider_has_its_own_budget(self):
        """**提供元で桁が違う**ので、見積もりは提供元ごとに持つ。

        `figure` が抜けていると `estimate_timeout()` は黙って `relation` の値に
        落ちる —— 落ちても動くので、抜けていることに気付けない。
        """
        for provider, each in llm.SECONDS_PER_ITEM.items():
            assert "figure" in each, provider
        assert ai.figure_timeout() > 0
