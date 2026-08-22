"""執筆（本文と辞書を対で育てる）の検算。

守っているのは 4 つ:

- **語だけでは通さない** —— 本文に足す一節が無ければ落とす。本文に出てこない
  表記は登録してもリンクにならないので、語と一節は対でしか受け取らない
- **入れたあとに実際にリンクになるかを見る** —— 素の部分一致では足りない。
  長い表記に食われる語や、大文字小文字を区別する表記はここでしか落ちない
- **入れる場所が分からなくても落とさない** —— 末尾へ足す
- **語数は決まった段のいずれか** —— 読めない値は既定に落ちる
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from glosspop import ai, config
from glosspop.app import app
from glosspop.core.linker import Linker
from glosspop.core.models import Entry, slugify


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

BODY = "# 影の経済\n\n序の文。\n\n## 仕組み\n\n本文 A。\n\n## 論点\n\n本文 B。\n"


def links_in(text: str, terms: list[str]) -> set[str]:
    """``Linker`` の規則で、その本文に実際に現れる語。"""
    entries = [
        Entry(term=t, slug=slugify(t), category="_", scope="local") for t in terms
    ]
    return {e.term for e in Linker(entries).entries_in(text)}


# --------------------------------------------------------------------------- #
# 語数の段
# --------------------------------------------------------------------------- #


def test_the_default_size_matches_extraction():
    """**入口が二つあって違う大きさの辞書ができる理由が無い。**"""
    assert ai.DEFAULT_COMPOSE_SIZE == 12
    assert ai.DEFAULT_COMPOSE_SIZE in ai.COMPOSE_SIZES


@pytest.mark.parametrize("bad", [None, "", "abc", 0, 99, -3, 13])
def test_unreadable_sizes_fall_back_to_the_default(bad):
    assert ai.normalize_size(bad) == ai.DEFAULT_COMPOSE_SIZE


def test_the_body_length_comes_from_the_size():
    """**欄を増やさないために語数から導く。** 段が上がれば分量も上がる。"""
    lengths = [ai.compose_chars(n) for n in ai.COMPOSE_SIZES]
    assert lengths == sorted(lengths)
    assert all(n <= ai.COMPOSE_MAX_CHARS for n in lengths)


def test_the_timeout_grows_with_the_size():
    """持ち時間は語数から見積もる（既定の 180 秒では足りない段がある）。"""
    assert ai.compose_timeout(40) > ai.compose_timeout(12)


# --------------------------------------------------------------------------- #
# 一節の挿入
# --------------------------------------------------------------------------- #


def test_a_passage_goes_at_the_end_of_its_section():
    out = ai.insert_passages(BODY, [{"term": "甲", "anchor": "仕組み", "passage": "甲の説明。"}])
    assert out.index("甲の説明。") > out.index("本文 A。")
    assert out.index("甲の説明。") < out.index("## 論点")


def test_an_unknown_anchor_still_lands():
    """**場所が分からないことを理由に落とさない** —— 落とすと語が登録できない。"""
    out = ai.insert_passages(BODY, [{"term": "乙", "anchor": "無い見出し", "passage": "乙の説明。"}])
    assert "乙の説明。" in out


def test_several_passages_all_land():
    items = [
        {"term": "甲", "anchor": "仕組み", "passage": "甲の説明。"},
        {"term": "乙", "anchor": "論点", "passage": "乙の説明。"},
        {"term": "丙", "anchor": "", "passage": "丙の説明。"},
    ]
    out = ai.insert_passages(BODY, items)
    for item in items:
        assert item["passage"] in out


# --------------------------------------------------------------------------- #
# 申告を信じない
# --------------------------------------------------------------------------- #


def test_a_term_without_a_passage_is_dropped():
    kept, dropped = ai.filter_needed(
        [{"term": "甲", "kind": "term", "passage": ""}], BODY, limit=12
    )
    assert not kept
    assert "一節" in dropped[0]["reason"]


def test_a_passage_that_lacks_the_term_is_dropped():
    """一節に語が無ければ、入れても本文にその表記は現れない。"""
    kept, dropped = ai.filter_needed(
        [{"term": "甲", "kind": "term", "passage": "この一節には出てきません。"}],
        BODY, limit=12,
    )
    assert not kept
    assert dropped[0]["reason"] == "一節の中にその表記がありません"


def test_a_term_shadowed_by_a_longer_one_is_dropped():
    """**素の部分一致では通ってしまう。** 同じ位置では長い表記が勝つ。"""
    raw = [
        {"term": "架空論", "kind": "term", "passage": "架空論とは何かを述べる。"},
        {"term": "架空", "kind": "term", "passage": "架空論について、さらに述べる。"},
    ]
    kept, dropped = ai.filter_needed(raw, BODY, limit=12)
    assert [k["term"] for k in kept] == ["架空論"]
    assert dropped[0]["term"] == "架空"
    assert "リンクになりません" in dropped[0]["reason"]


def test_a_short_acronym_inside_an_extension_is_dropped():
    """3 文字以下の全大文字 ASCII は区別される（`MD` が `README.md` に当たらない）。"""
    raw = [{"term": "ZZ", "kind": "term", "passage": "ファイル名は sample.zz です。"}]
    kept, dropped = ai.filter_needed(raw, BODY, limit=12)
    assert not kept
    assert "リンクになりません" in dropped[0]["reason"]


def test_kept_terms_really_link_after_insertion():
    """採用したものは、入れた本文で必ずリンクになる（これが本物の検算）。"""
    raw = [
        {"term": "灰色市場", "kind": "term", "anchor": "仕組み",
         "passage": "この取引は灰色市場と呼ばれる。記録には残らない。"},
        {"term": "帳簿外", "kind": "term", "anchor": "論点",
         "passage": "帳簿外の取引をどう数えるかは決着していない。"},
    ]
    kept, _ = ai.filter_needed(raw, BODY, limit=12)
    out = ai.insert_passages(BODY, kept)
    assert links_in(out, [k["term"] for k in kept]) == {k["term"] for k in kept}


def test_the_quota_is_per_kind():
    """種別ごとの枠は `filter_candidates()` と同じ `allocate_quota()`。"""
    raw = [
        {"term": f"語{i}", "kind": "term", "passage": f"語{i}についての説明。"}
        for i in range(10)
    ]
    kept, dropped = ai.filter_needed(raw, BODY, limit=3, kinds=["term"])
    assert len(kept) == 3
    assert any("枠" in d["reason"] for d in dropped)


# --------------------------------------------------------------------------- #
# プロンプト
# --------------------------------------------------------------------------- #


def test_the_compose_prompt_asks_the_ai_to_choose_when_nothing_is_given():
    """完全ランダム指定でも、欄が空なだけで同じ 1 つのプロンプトで済む。"""
    prompt = ai.build_compose_prompt()
    assert "あなたが 1 つ選んで" in prompt
    assert "あなたが 1 つ立てて" in prompt
    for genre in ai.genre_labels():
        assert genre in prompt


def test_the_compose_prompt_carries_the_theme_and_the_size():
    prompt = ai.build_compose_prompt(genre="歴史", theme="ある仮定", size=25)
    assert "ある仮定" in prompt and "歴史" in prompt
    assert str(ai.compose_chars(25)) in prompt
    assert "25 語" in prompt


def test_the_style_applies_to_composing(monkeypatch):
    """**抽出には効かせないが、こちらは本文を書くので効く。**"""
    monkeypatch.setattr(ai, "style", lambda: "講談調で書くこと。")
    assert "講談調で書くこと。" in ai.build_compose_prompt()


def test_the_needed_prompt_demands_a_passage():
    prompt = ai.build_needed_prompt(BODY, limit=12)
    assert "passage" in prompt
    assert "本文に足す一節" in prompt


# --------------------------------------------------------------------------- #
# 口
# --------------------------------------------------------------------------- #


@pytest.fixture
def stubbed(monkeypatch):
    """AI を差し替える。**`claude` の有無をテストの前提にしない。**"""
    answers = {
        "compose": BODY,
        "needed": json.dumps([
            {"term": "灰色市場", "kind": "term", "anchor": "仕組み",
             "passage": "この取引は灰色市場と呼ばれる。記録には残らない。"},
        ], ensure_ascii=False),
    }

    def fake(prompt, timeout=None):
        return answers["compose"] if "本文を 1 枚書いて" in prompt else answers["needed"]

    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "_generate", fake)
    return answers


def test_compose_options_do_not_use_numbers_as_json_keys(client):
    """JSON のオブジェクトキーは文字列になるので、数を鍵にしない。"""
    data = client.get("/api/ai/compose-options").json()
    assert [s["size"] for s in data["sizes"]] == list(ai.COMPOSE_SIZES)
    assert data["default_size"] == ai.DEFAULT_COMPOSE_SIZE
    assert all(g["label"] and g["hint"] for g in data["genres"])


def test_compose_returns_the_body_without_saving(client, stubbed, tmp_path):
    before = set(tmp_path.rglob("*.md"))
    res = client.post("/api/ai/compose", json={"genre": "社会・制度", "theme": "記録", "size": 12})
    assert res.status_code == 200
    assert res.json()["text"].startswith("#")
    assert set(tmp_path.rglob("*.md")) == before, "執筆は保存しない"


def test_compose_strips_a_wrapping_code_fence(client, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "_generate", lambda p, timeout=None: f"```markdown\n{BODY}\n```")
    assert client.post("/api/ai/compose", json={}).json()["text"].startswith("# 影の経済")


def test_needed_and_insert_round_trip(client, stubbed):
    res = client.post("/api/ai/needed", json={"text": BODY, "limit": 12})
    kept = res.json()["candidates"]
    assert [k["term"] for k in kept] == ["灰色市場"]

    out = client.post("/api/ai/insert", json={"text": BODY, "items": kept}).json()["text"]
    assert links_in(out, ["灰色市場"]) == {"灰色市場"}


def test_the_ai_paths_answer_503_without_a_provider(client, monkeypatch):
    """**`claude` の有無をテストの前提にしない** —— 明示的に無い側も見る。"""
    monkeypatch.setattr(ai, "available", lambda: False)
    for path, body in (
        ("/api/ai/compose", {}),
        ("/api/ai/needed", {"text": BODY}),
    ):
        assert client.post(path, json=body).status_code == 503


def test_insert_never_calls_the_ai(client, monkeypatch):
    """一節を入れるのは入出力だけ。提供元が無くても通る。"""
    monkeypatch.setattr(ai, "available", lambda: False)
    res = client.post(
        "/api/ai/insert",
        json={"text": BODY, "items": [{"term": "甲", "anchor": "仕組み", "passage": "甲の説明。"}]},
    )
    assert res.status_code == 200 and "甲の説明。" in res.json()["text"]


# --------------------------------------------------------------------------- #
# 保存（**利用者の文書を書く唯一の口**）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "title,want",
    [
        ("# 影の経済\n\n本文。", "影の経済.md"),
        ("題の無い本文", "無題.md"),
        ("# a/b:c*d?e", "abcde.md"),
        ("# CON", "_CON.md"),
        ("#    ", "無題.md"),
    ],
)
def test_the_filename_comes_from_the_title(title, want):
    """**`slugify()` は使わない** —— 文書の名前は人が見て題と分かるほうがよい。"""
    assert ai.suggest_filename(title) == want


@pytest.mark.parametrize(
    "bad",
    ["../そと", "..\\そと", "/etc/passwd", "a/b/c", "   ", ".", "..", "\x01\x02"],
)
def test_a_hostile_name_never_leaves_the_folder(client, bad):
    """**外から来た名前をそのまま繋がない**（zip の展開と同じ規則）。"""
    res = client.post("/api/compose/save", json={"text": BODY, "name": bad})
    assert res.status_code in (200, 409)
    root = config.content_dir().resolve()
    written = list(root.rglob("*.md"))
    assert written, "どこかには書かれているはず"
    for path in written:
        assert path.parent == root, f"{path} がフォルダの外に出た"


def test_saving_writes_into_the_open_folder(client):
    res = client.post("/api/compose/save", json={"text": BODY})
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "影の経済.md" and data["overwritten"] is False
    written = config.content_dir() / "影の経済.md"
    assert written.read_text(encoding="utf-8").startswith("# 影の経済")


def test_an_existing_file_is_never_overwritten_silently(client):
    """**黙って上書きしない。** 文書には控えの仕組みが無いので、先に見せるしかない。"""
    assert client.post("/api/compose/save", json={"text": BODY}).status_code == 200

    again = client.post("/api/compose/save", json={"text": BODY + "\n追記\n"})
    assert again.status_code == 409
    assert "すでにあります" in again.json()["detail"]
    kept = (config.content_dir() / "影の経済.md").read_text(encoding="utf-8")
    assert "追記" not in kept, "断ったのに書き換わっている"

    forced = client.post(
        "/api/compose/save", json={"text": BODY + "\n追記\n", "overwrite": True}
    )
    assert forced.status_code == 200 and forced.json()["overwritten"] is True
    assert "追記" in (config.content_dir() / "影の経済.md").read_text(encoding="utf-8")


def test_an_empty_body_is_refused(client):
    assert client.post("/api/compose/save", json={"text": "   "}).status_code == 400


def test_the_target_is_reported_before_writing(client):
    """**押す前に、どこへ書くかと上書きになるかを返す**（画面に出すための材料）。"""
    first = client.get("/api/compose/target", params={"name": "影の経済"}).json()
    assert first["name"] == "影の経済.md"
    assert first["root"] == str(config.content_dir())
    assert first["exists"] is False

    client.post("/api/compose/save", json={"text": BODY})
    again = client.get("/api/compose/target", params={"name": "影の経済"}).json()
    assert again["exists"] is True


def test_asking_where_it_would_go_creates_nothing(client):
    before = sorted(p.name for p in config.content_dir().iterdir())
    client.get("/api/compose/target", params={"name": "まだ無い名前"})
    assert sorted(p.name for p in config.content_dir().iterdir()) == before


def test_saving_follows_the_folder_that_is_open(client, tmp_path):
    """**基準は必ず `config.content_dir()`** —— 切り替えたら書き先も移る。"""
    other = tmp_path / "べつのフォルダ"
    other.mkdir()
    config.set_content_dir(other)
    res = client.post("/api/compose/save", json={"text": BODY})
    assert res.status_code == 200
    assert (other / "影の経済.md").is_file()
