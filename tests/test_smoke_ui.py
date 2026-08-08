"""ブラウザで実際に動かす通しテスト。

このリポジトリの機能は「登録 → 本文でリンクになる → 吹き出しが出る →
関係が図になる」まで通して見ないと確認できない。単体テストはどれも
その手前で止まる（HTML 文字列は正しいのに JS が落ちている、が起きる）。

**手元の Chrome を使う**（``channel="chrome"``）。ブラウザを別途ダウンロードさせ
ないため。Chrome も playwright も無い環境では丸ごと skip する —— 通常の
``uv run pytest`` を止めないのが条件。

    uv run playwright install chromium   # 手元に Chrome が無いときだけ
"""

from __future__ import annotations

import json
import pathlib
import socket
import threading
import time
from urllib.parse import quote

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright が入っていません")

from glosspop import config, store, updates  # noqa: E402
from glosspop.core.models import EntryDraft  # noqa: E402

#: このファイルは丸ごと `smoke`。日常の反復から外せるようにするための印で、
#: **リリース前 (`check.cmd ci`) は外さない**（外れると JS の壊れが素通りする）。
#: 印は pyproject.toml の `markers` にも登録してある。
pytestmark = pytest.mark.smoke


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(isolated_dirs):
    """使い捨ての辞書で本物のサーバを立てる。

    ``isolated_dirs``（autouse fixture）が差し替えたディレクトリをそのまま使う。
    サーバは同じプロセスの別スレッドなので、モジュール変数の差し替えが効く。
    """
    import uvicorn

    from glosspop.app import app

    # **画面を開くと update.js が /api/update を叩き、サーバが GitHub を見に行く。**
    # 確認済みの結果を入れておけば通信せずに済み、お知らせの表示も一緒に確かめられる
    config.save_settings({
        "update_last_checked": int(time.time()),
        "update_latest": "v99.0.0",
    })
    updates.invalidate()

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("テスト用サーバが起動しませんでした")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        updates.invalidate()


def _watch_page(page) -> dict[str, list[str]]:
    """画面の裏で起きたことを集める。

    見るのは 3 つとも「**落ちた理由がここにしか残らない**」もの:

    - ``pageerror`` … JS の例外。握り潰されると画面には何も出ない
    - ``console`` の error … 例外にならない失敗（fetch の握り潰しなど）
    - 通信 … 繋がらなかった要求と、4xx / 5xx を返した応答

    「要素が出てこない」の原因はたいていこのどれかで、**待ち時間の切れた
    タイムアウトだけが残ると原因が消える**（リリースで実際に踏んだ）。
    """
    seen: dict[str, list[str]] = {"js": [], "console": [], "network": []}
    page.on("pageerror", lambda e: seen["js"].append(str(e)))
    page.on(
        "console",
        lambda m: m.type == "error" and seen["console"].append(m.text),
    )
    page.on(
        "requestfailed",
        lambda r: seen["network"].append(f"{r.method} {r.url} → {r.failure}"),
    )
    page.on(
        "response",
        lambda r: r.status >= 400 and seen["network"].append(f"{r.status} {r.url}"),
    )
    return seen


def _notes(seen: dict[str, list[str]]) -> list[str]:
    labels = {"js": "JS の例外", "console": "console の error", "network": "通信の失敗"}
    return [
        f"{labels[key]}:\n  " + "\n  ".join(lines)
        for key, lines in seen.items() if lines
    ]


@pytest.fixture
def page(server):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception as exc:                       # noqa: BLE001
            pytest.skip(f"Chrome を起動できません: {exc}")
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        seen = _watch_page(page)
        try:
            yield page
        finally:
            # **本体が落ちたときは、ここで出さないと原因が残らない。**
            # fixture は**テスト本体の例外を受け取れない**（pytest は例外を
            # generator へ投げ込まず、teardown を普通に進める）ので、`except` で
            # 拾って例外に書き添える手は使えない —— 実際に書いてみて、`else` の
            # ほうが走ることを確かめた。失敗したテストの出力は pytest が
            # 「Captured stdout teardown」として出すので、そこへ流す
            for note in _notes(seen):
                print(note)
            context.close()
            browser.close()
        # JS の例外は画面に出ないことがある。黙って壊れたまま通さない
        assert not seen["js"], f"ページで JS エラーが出ました: {seen['js']}"


#: 画面が落ち着くまでに待つ時間。**1 か所に置く。** 呼び出しごとに数字を散らすと、
#: 「遅いだけ」と「壊れている」を切り分けるときに全部直して回ることになる
SETTLE_MS = 15000


def open_glossary(page, server, query: str = ""):
    """辞書一覧を開いて、**最初の描画が終わるまで**待つ。

    `.card` が出るのを直接待つと、`reload()` が失敗して `.status.error` を出した
    場合も**ただの 15 秒タイムアウト**になり、理由が残らない（`test_the_glossary_
    filters_by_tag` が CI でこの落ち方をして、リリースを止めた）。

    `#list` は読み込み中だけ `aria-busy` を持ち、終わると必ず何かを描く
    （カード / 「該当する用語がありません」/ エラー）。つまり「busy でない
    **かつ** 中身がある」が「1 回描き終わった」の合図になる。**画面の側に
    テスト用の目印を足していない**ので、本番の挙動をそのまま見ている。
    """
    page.goto(f"{server}/glossary{query}")
    wait_for_glossary(page)


def open_other_source(page):
    """「その他の開き方」を開く。

    1 つだけのファイルと貼り付けは**ダイアログの中**にある（読み続けるための
    入口＝フォルダ / URL とは別扱い）。開かずに `#paste` を触ると、隠れている
    要素への操作としてタイムアウトする。
    """
    page.click("#otherSource")
    page.locator("dialog.sheet[open] #paste").wait_for(timeout=SETTLE_MS)


def open_content_search(page):
    """横断検索を開く。**既定では畳んである**（毎回は使わない）。"""
    if not page.locator("#searchFold[open]").count():
        page.click("#searchFold > summary")
    page.locator("#contentQ").wait_for(state="visible", timeout=SETTLE_MS)


def wait_for_glossary(page):
    page.wait_for_function(
        "() => { const l = document.getElementById('list');"
        " return l && !l.hasAttribute('aria-busy') && l.children.length > 0; }",
        timeout=SETTLE_MS,
    )
    # 描き終わったうえで中身がエラーなら、その文言を持って落ちる
    failed = page.locator("#list .status.error")
    if failed.count():
        raise AssertionError(f"一覧の読み込みに失敗しました: {failed.first.text_content()}")


@pytest.fixture
def seeded(isolated_dirs):
    """人物 2 人と、その名前が出てくる本文。"""
    store.save(EntryDraft(
        term="ジョバンニ", category="登場人物",
        summary="活版所で働く少年。", definition="主人公。",
    ))
    store.save(EntryDraft(
        term="カムパネルラ", category="登場人物",
        summary="ジョバンニの級友。", definition="同級生。",
    ))
    doc = config.content_dir() / "銀河.md"
    doc.write_text(
        "# 午后の授業\n\nジョバンニは活版所で働いていた。カムパネルラは黙っていた。\n",
        encoding="utf-8",
    )
    return doc


def test_registered_terms_become_links_with_a_popup(page, server, seeded):
    """登録 → 本文でリンクになる → 吹き出しが出る、まで通す。"""
    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    link = page.locator("a.gloss-link", has_text="ジョバンニ").first
    link.wait_for(timeout=15000)
    assert page.locator("a.gloss-link").count() >= 2

    link.hover()
    popup = page.locator(".gloss-pop")
    popup.wait_for(timeout=5000)
    assert "活版所で働く少年" in popup.inner_text()


def test_the_persona_face_shows_in_the_popup(page, server, seeded):
    """語り手の顔が吹き出しに出ること。**画像は実際に配信されるところまで見る。**

    HTML に `<img>` が出ていても、配る口が無ければ壊れた画像が並ぶだけになる。
    """
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c630001000005000101"
        "0d0a2db40000000049454e44ae426082"
    )
    (config.CATEGORIES_FILE.parent).mkdir(parents=True, exist_ok=True)
    (config.CATEGORIES_FILE.parent / "persona.png").write_bytes(png)

    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    link = page.locator("a.gloss-link", has_text="ジョバンニ").first
    link.wait_for(timeout=15000)
    link.hover()

    face = page.locator(".gloss-pop img.pop-face")
    face.wait_for(timeout=10000)
    # 実際に読めた画像かどうか（壊れていれば naturalWidth が 0）
    assert face.evaluate("img => img.complete && img.naturalWidth > 0")


def test_a_term_inside_the_popup_is_followed_in_place(page, server, isolated_dirs):
    """**吹き出しの中の用語は、その吹き出しの中で辿る。**

    新しく開こうとすると `paint()` が innerHTML を差し替え、**いまポインタが指して
    いる要素ごと消える** —— 指す先を失った離脱で元の吹き出しまで閉じる
    （「両方消える」の正体）。押した先はその場で入れ替わり、← で戻れること。
    """
    store.save(EntryDraft(
        term="カムパネルラ", category="登場人物",
        summary="ジョバンニの級友。", definition="同級生。",
    ))
    store.save(EntryDraft(
        term="ジョバンニ", category="登場人物",
        summary="活版所で働く少年。", definition="カムパネルラと同じ組の少年。",
    ))
    (config.content_dir() / "銀河.md").write_text(
        "# 午后の授業\n\nジョバンニは活版所で働いていた。\n", encoding="utf-8"
    )

    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    link = page.locator("a.gloss-link", has_text="ジョバンニ").first
    link.wait_for(timeout=15000)
    link.hover()
    popup = page.locator(".gloss-pop")
    popup.wait_for(timeout=5000)

    inner = popup.locator("a.gloss-link", has_text="カムパネルラ").first
    inner.wait_for(timeout=5000)
    inner.click()

    # 消えない。中身だけが相手の語に入れ替わる
    page.locator(".gloss-pop [data-pop-back]").wait_for(timeout=5000)
    assert popup.is_visible()
    assert "級友" in popup.inner_text()

    page.click(".gloss-pop [data-pop-back]")
    page.locator(".gloss-pop .pop-term", has_text="ジョバンニ").wait_for(timeout=5000)
    assert popup.is_visible()
    assert "活版所で働く少年" in popup.inner_text()


def test_an_existing_entry_can_be_rewritten_by_ai(page, server, seeded):
    """**編集でも「AI で書き直す」が出ること。**

    前は本文があるとボタンごと消していたので、文体（口調）を変えても登録済みの語を
    書き直す手段が無かった（実際に困った）。AI は呼ばず、口があることだけを見る。
    """
    page.goto(f"{server}/glossary/登場人物/ジョバンニ")
    page.locator("button:has-text('編集')").first.wait_for(timeout=15000)
    page.click("button:has-text('編集')")

    draft = page.locator("dialog.sheet[open] [data-ref=draft]")
    draft.wait_for(timeout=10000)
    assert draft.is_visible()
    assert "書き直す" in draft.inner_text()
    # 本文は消えていない（書き直しは押したときだけ）
    assert page.input_value("dialog.sheet[open] [data-ref=definition]") == "主人公。"


def test_a_relation_can_be_added_and_shows_on_both_sides(page, server, seeded):
    """関係は片側にだけ書き、相手のページには逆引きで出る。"""
    page.goto(f"{server}/glossary/登場人物/ジョバンニ")
    page.locator("input[aria-label='関係の相手']").wait_for(timeout=15000)
    page.fill("input[aria-label='関係の相手']", "カムパネルラ")
    page.fill("input[aria-label='関係']", "親友")
    page.fill("input[aria-label='逆からの関係']", "親友")
    page.click("button:has-text('関係を足す')")

    row = page.locator(".rel-list .rel-row").first
    row.wait_for(timeout=10000)
    assert "カムパネルラ" in row.inner_text() and "親友" in row.inner_text()
    assert "⇄" in row.inner_text()          # back があるので相互

    # 書いていない側にも見えること
    page.goto(f"{server}/glossary/登場人物/カムパネルラ")
    page.locator("text=この語を指している側").wait_for(timeout=10000)
    assert "ジョバンニ" in page.locator(".rel-list").last.inner_text()


def test_one_term_can_ask_for_its_own_relations(page, server, seeded):
    """用語ページから**その語の**関係を下書きできる。

    まとめての下書きはビューアにあるが、それは文書に出てくる語ぜんぶが対象。
    「この語だけ関係が空のまま」を埋める道が用語ページに無かった。
    AI は呼ばない（ここで見るのは、ボタンが正しい相手でダイアログを開くこと）。
    """
    page.goto(f"{server}/glossary/登場人物/ジョバンニ")
    page.locator("button:has-text('この語の関係を下書き')").wait_for(timeout=15000)
    page.click("button:has-text('この語の関係を下書き')")

    lead = page.locator("dialog.sheet[open] [data-ref=lead]")
    lead.wait_for(timeout=10000)
    assert "「ジョバンニ」の関係" in (lead.text_content() or "")
    # 1 語ぶんは探す本数の既定を下げる（本数はそのまま待ち時間になる）
    assert page.locator("dialog.sheet[open] [data-ref=limit]").input_value() == "10"
    page.click("dialog.sheet[open] [data-ref=cancel]")


def test_the_graph_draws_nodes_and_an_edge(page, server, seeded):
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"}],
        ),
        ref=a.ref,
    )
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    assert page.locator("svg.rel-graph .rel-node").count() == 2
    assert page.locator("svg.rel-graph .rel-edge").count() == 1
    assert "親友" in (page.locator("svg.rel-graph").text_content() or "")


def test_the_graph_keeps_unrelated_terms_out_of_the_ranks(page, server, seeded):
    """関係の無い語を段に混ぜない（混ぜると繋がっている語どうしを押し広げる）。

    20 語に満たない辞書でも図が読めなくなった実例が、まさにこれだった ——
    18 語のうち 6 語が孤立していて、いちばん多く繋がっている語が最上段の
    右端へ追いやられ、そこから全部の線が図の端まで飛んでいた。

    **消しはしない**（その文書に出てくる語ではある）ので、段の外の帯に並べる。
    黙って別のところへ置くと「下にあるから下位」に見えるため、区切り線と
    語数も一緒に見る。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"}],
        ),
        ref=a.ref,
    )
    for name in ["ザネリ", "カムパネルラの父", "鳥捕り", "灯台守", "牛乳屋"]:
        store.save(EntryDraft(term=name, category="登場人物", definition="関係は書かない。"))

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.wait_for_function(
        "() => document.querySelectorAll('svg.rel-graph .rel-node').length === 7",
        timeout=15000,
    )
    placed = page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-graph .rel-node')].map((g) => ({"
        " term: g.querySelector('text').textContent,"
        " y: Number(g.querySelector('rect').getAttribute('y')) }))"
    )
    linked = [p["y"] for p in placed if p["term"] in ("ジョバンニ", "カムパネルラ")]
    lonely = [p["y"] for p in placed if p["term"] not in ("ジョバンニ", "カムパネルラ")]
    assert len(linked) == 2 and len(lonely) == 5
    # 関係の無い語は段より下（＝段の並びに割り込んでいない）
    assert min(lonely) > max(linked)
    assert page.locator("svg.rel-graph .rel-lonely-rule").count() == 1
    assert "5" in (page.locator("svg.rel-graph .rel-lonely-caption").text_content() or "")
    assert "段の外" in (page.locator("#legend").text_content() or "")


def test_the_graph_lights_a_relation_and_its_words_together(page, server, seeded):
    """線と一言は一緒に光る。一言は線より上の層に居る。

    一言は空いているところへ逃がすので**線の真上とは限らない**。線だけ色が
    変わっても、どの関係が選ばれているのか分からない（実際にそう見えた）。

    層を分けているのは、**あとに描いた辺の線が前の辺の一言を横切る**から。
    下になった文字は縁取りごと消えて読めなくなる。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"}],
        ),
        ref=a.ref,
    )
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph .rel-edge-label").wait_for(timeout=15000)
    hot = "() => [document.querySelectorAll('.rel-edge-group.hot').length," \
          " document.querySelectorAll('.rel-edge-label.hot').length]"

    page.locator("svg.rel-graph .rel-edge-group").hover()
    page.wait_for_timeout(150)
    assert page.evaluate(hot) == [1, 1], "線に乗せても一言が光らない"

    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    assert page.evaluate(hot) == [0, 0], "離れても光ったまま"

    # 一言のほうに乗せても線が光る（どちらから触っても同じ関係だと分かる）
    page.locator("svg.rel-graph .rel-edge-label").hover()
    page.wait_for_timeout(150)
    assert page.evaluate(hot) == [1, 1], "一言に乗せても線が光らない"

    # 焦点でも同じ
    page.mouse.move(0, 0)
    page.locator("svg.rel-graph .rel-edge-group").focus()
    page.wait_for_timeout(150)
    assert page.evaluate(hot) == [1, 1], "焦点では光らない"

    layers = page.evaluate(
        "() => [...document.querySelector('svg.rel-graph').children]"
        ".map((n) => n.getAttribute('class') || n.tagName)"
    )
    assert layers.index("rel-edge-lines") < layers.index("rel-edge-labels"), "一言が線より下"
    # 一言そのものの文字は一言だけ（<text> の中に <title> を入れない）
    assert page.locator("svg.rel-graph .rel-edge-label").text_content() == "親友"


def test_the_graph_has_a_crossing_free_mode(page, server, seeded):
    """見せ方を「交差しない図」に切り替えられる。

    段の図で交差が消せないのは `rank` でノードが段に固定されているから
    （→ docs/design-notes.md）。こちらは**関係 1 本ごとに独立した列**を与えて
    その制約ごと外している。見張るのはその性質そのもの —— **どの 2 本の縦線も
    同じ列に居ない**。同じ列に載せる実装に戻ると、絵は似ているのに交差する。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    zanelli = store.save(EntryDraft(term="ザネリ", category="登場人物", definition="級友。"))
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[
                {"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"},
                {"to": zanelli.ref, "label": "同級生", "rank": "対等"},
            ],
        ),
        ref=a.ref,
    )
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)

    page.select_option("#mode", "fabric")
    page.locator("svg.rel-fabric").wait_for(timeout=10000)
    page.wait_for_timeout(200)
    # 用語は横線、関係は縦線。関係の本数だけ列がある
    assert page.locator("svg.rel-fabric .rel-edge-group").count() == 2
    assert page.locator("svg.rel-fabric .rel-node").count() == 3
    xs = page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-fabric .rel-edge-group .rel-edge')]"
        ".map((l) => Number(l.getAttribute('x1')))"
    )
    assert len(set(xs)) == len(xs), f"2 本が同じ列に載っている: {xs}"
    assert "交差しません" in (page.text_content("#legend") or "")

    # 線を押せば同じ編集ダイアログが開く（見せ方が変わっても直し方は 1 つ）
    page.click("svg.rel-fabric .rel-edge-group")
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)
    page.keyboard.press("Escape")

    # 選んだ見せ方は覚えている（覆いは何度でも開き直される）
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-fabric").wait_for(timeout=10000)
    assert page.locator("#mode").input_value() == "fabric"

    # 戻せること。戻したら段の図（サーバへは行き直さない）
    page.select_option("#mode", "layered")
    page.locator("svg.rel-graph:not(.rel-fabric)").wait_for(timeout=10000)


#: 地図に使う絵。**寸法を書いておく** —— `map.js` は縦横比が届いてから高さを直すので、
#: 内在サイズの無い SVG では実際の高さが決まらない
TEST_MAP = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 500" '
    'width="1000" height="500"><rect width="1000" height="500" fill="#dcd8cc"/></svg>'
)


def _put_test_map(name: str = "てすと図") -> None:
    directory = store.maps_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.svg").write_text(TEST_MAP, encoding="utf-8")


def test_the_graph_has_a_map_mode(page, server, seeded):
    """座標を書いた語を絵の上に置く見せ方。

    **座標が書いてあるものだけが出る**（種別やタグで「どれが地名か」を決めない）。
    出していないものは必ず数えて凡例に出す —— ここが緩むと、絵に写っていない語を
    黙って落とした図になる。線を押せば他の見せ方と同じ編集ダイアログが開く。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    # ザネリには座標を書かない —— **数えて凡例に出ること**を見るため
    store.save(EntryDraft(term="ザネリ", category="登場人物", definition="級友。"))
    store.save(
        EntryDraft(
            term=b.term, category=b.category, summary=b.summary, definition=b.definition,
            map="てすと図", pin=[0.62, 0.44],
        ),
        ref=b.ref,
    )
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            map="てすと図", pin=[0.24, 0.30],
            relations=[{"to": b.ref, "label": "並んで歩く"}],
        ),
        ref=a.ref,
    )

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    page.wait_for_timeout(200)

    # 形を書いた 2 語だけが出る（ザネリは出ない）
    assert page.locator("svg.rel-map .rel-map-pin").count() == 2
    assert page.locator("svg.rel-map .rel-edge-group").count() == 1
    # 絵は `<image>` の中（CSS の背景にすると viewBox で一緒に動かせない）
    href = page.get_attribute("svg.rel-map image.rel-map-bg", "href")
    assert href and href.startswith("/api/map?"), href

    # **出していないものを黙らない。** どの絵かも書く
    legend = page.text_content("#legend") or ""
    assert "てすと図" in legend and "1 語" in legend, legend

    # **点は絵の幅を 1 とした比で置かれる**（幅 1000 の絵なので 0.24 → 240）。
    # 縦横それぞれに 0〜1 を割り当てる形に戻すと、縦横比の違う絵で点が歪む ——
    # ここは絵の高さ (500) ではなく**幅**で割っていることを見ている
    spots = page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-map .rel-map-pin circle')]"
        ".map((c) => [Math.round(+c.getAttribute('cx')), Math.round(+c.getAttribute('cy'))])"
    )
    assert sorted(map(tuple, spots)) == [(240, 300), (620, 440)], spots

    # 線を押せば同じ編集ダイアログ（見せ方が変わっても直し方は 1 つ）
    page.click("svg.rel-map .rel-edge-group")
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)
    page.keyboard.press("Escape")


def test_the_map_draws_points_lines_and_areas(page, server, seeded):
    """形は 3 つ（点・線・領域）。**重ねる順は 領域 → 線 → 点。**

    領域を後に描くと点を塗りつぶす。3 つとも同じ入れ物に混ぜると、描いた順で
    見え方が変わる（絵は出るので、画面を見るまで気付けない類）。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.24, 0.30],
    ), ref=a.ref)
    store.save(EntryDraft(
        term=b.term, category=b.category, definition=b.definition,
        map="てすと図", line=[[0.1, 0.1], [0.5, 0.2], [0.9, 0.1]],
    ), ref=b.ref)
    store.save(EntryDraft(
        term="ザネリ", category="登場人物", definition="級友。",
        map="てすと図", area=[[0.1, 0.35], [0.6, 0.35], [0.35, 0.6]],
    ))

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    page.wait_for_timeout(200)

    assert page.locator("svg.rel-map .rel-map-point circle").count() == 1
    assert page.locator("svg.rel-map .rel-map-line polyline.rel-map-route").count() == 1
    assert page.locator("svg.rel-map .rel-map-area polygon").count() == 1
    # 内訳も凡例に出す（何を出したのかを黙らない）
    assert "点 1 / 線 1 / 領域 1" in (page.text_content("#legend") or "")

    # **重ねる順**。領域がいちばん下、点がいちばん上
    order = page.evaluate(
        "() => [...document.querySelector('svg.rel-map').children]"
        ".map((g) => g.querySelector('.rel-node')?.classList.contains('rel-map-area') ? 'area'"
        "  : g.querySelector('.rel-node')?.classList.contains('rel-map-line') ? 'line'"
        "  : g.querySelector('.rel-node')?.classList.contains('rel-map-point') ? 'point' : '')"
        ".filter(Boolean)"
    )
    assert order == ["area", "line", "point"], order


def test_overlapping_shapes_get_their_own_colour(page, server, seeded):
    """**重なる形は色で分ける。** 同色だと、どれがどれかは一言でしか分からない
    —— その一言は重なれば畳まれるので、いちばん見せたいものが読めなくなる。

    **点には振らない**（場所が 1 つなので取り違えようがない）。**色を作るのは
    `map.js` の 1 か所**で、一覧の色札はそれをそのまま出す —— 2 か所で作ると
    図と一覧で色が食い違い、色を頼りに探せなくなる。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    # 同じ点から分かれる 2 本（説ごとの進軍路にあたる形）。根元は完全に重なる
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", line=[[0.2, 0.2], [0.5, 0.1]],
    ), ref=a.ref)
    store.save(EntryDraft(
        term=b.term, category=b.category, definition=b.definition,
        map="てすと図", line=[[0.2, 0.2], [0.5, 0.4]],
    ), ref=b.ref)
    store.save(EntryDraft(
        term="ザネリ", category="登場人物", definition="級友。",
        map="てすと図", pin=[0.8, 0.3],
    ))

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    page.wait_for_timeout(200)

    strokes = page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-map polyline.rel-map-route')]"
        ".map((p) => getComputedStyle(p).stroke)"
    )
    assert len(strokes) == 2 and strokes[0] != strokes[1], strokes

    # **点には振らない**（変数を置かない ＝ CSS の既定に収まる）
    assert page.evaluate(
        "() => document.querySelector('svg.rel-map .rel-map-point')"
        ".style.getPropertyValue('--shape-color')"
    ) == ""

    # 一覧の色札は図と同じ色。**点のぶんは出ない**
    swatches = page.evaluate(
        "() => [...document.querySelectorAll('#mapLayers .map-swatch')]"
        ".map((s) => getComputedStyle(s).backgroundColor)"
    )
    assert sorted(swatches) == sorted(strokes), (swatches, strokes)


def test_a_shape_cannot_be_moved_off_the_picture(page, server, seeded):
    """**絵の外へは出させない。** 外に出た点は座標だけ書かれて**画面に出ない**
    ので、点検を開くまで気付けない（地図の「黙って壊れる」の代表）。

    上限の高さは**絵が届いてから**決まる（縦横比は読み込むまで分からない）ので、
    絵の高さが入ったことを確かめてから動かす。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.9, 0.45],
    ), ref=a.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    page.check("#mapLayers [data-ref=mapEdit] input")
    page.locator("svg.rel-map .rel-map-handle").first.wait_for(timeout=10000)
    # **描き直したあとで待つ。** 「置く」を入れると図を描き直すので、そこでも
    # 仮の高さ (700) から始まる —— 本物 (500) が入る前に動かすと縁が違う
    page.wait_for_function(
        "() => document.querySelector('svg.rel-map image.rel-map-bg')"
        "?.getAttribute('height') === '500'",
        timeout=10000,
    )
    # 掴んだまま図の外まで引っぱる（矢印キーも同じところを通る）
    handle = page.locator("svg.rel-map .rel-map-handle").first
    spot = handle.bounding_box()
    stage = page.locator("svg.rel-map").bounding_box()
    page.mouse.move(spot["x"] + spot["width"] / 2, spot["y"] + spot["height"] / 2)
    page.mouse.down()
    page.mouse.move(
        stage["x"] + stage["width"] - 2, stage["y"] + stage["height"] - 2, steps=8
    )
    page.mouse.up()
    page.wait_for_timeout(600)

    stopped = store.get(a.ref).pin
    # 幅は 1.0、高さは絵の縦横比 (500/1000) が上限
    assert stopped == pytest.approx([1.0, 0.5]), stopped


def test_the_keyboard_can_move_a_vertex_more_than_once(page, server, seeded):
    """**保存で描き直しても、触っていた頂点に焦点を戻す。**

    地図は掴んで離したら書く（保存が即時）ので、**書くたびに図ごと差し替わる**。
    戻さないと焦点が body へ落ち、**矢印キーは 1 回しか効かない**（実測: 1 回目で
    0.30 → 0.32、2 回目は動かず `activeElement` は BODY）。マウスは 1 ドラッグ
    1 保存なので気付けず、「掴めない人を締め出さない」と書いてある側だけが
    効いていなかった。**ここが緩んでも画面は正常に見える。**
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", line=[[0.30, 0.30], [0.60, 0.40]],
    ), ref=a.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    page.check("#mapLayers [data-ref=mapEdit] input")
    page.locator("svg.rel-map .rel-map-handle").first.wait_for(timeout=10000)
    page.locator("svg.rel-map .rel-map-handle").first.focus()

    # 1 押し 20px（絵の幅 1000）＝ 0.02 ずつ。**2 回押せば 2 回ぶん動くこと**
    for _ in range(2):
        page.keyboard.press("Shift+ArrowRight")
        page.wait_for_timeout(500)
    assert store.get(a.ref).line[0] == pytest.approx([0.34, 0.30]), store.get(a.ref).line
    # 焦点は同じ頂点に残っている（body へ落ちない）
    assert page.evaluate(
        "() => document.activeElement?.classList?.contains('rel-map-handle') || false"
    )

    # 足すのも同じ経路。**押した頂点に焦点が残る**（足したぶんは隣に入る）
    page.keyboard.press("Insert")
    page.wait_for_timeout(600)
    assert page.locator("svg.rel-map .rel-map-handle").count() == 3
    assert page.evaluate(
        "() => document.activeElement?.getAttribute('data-vertex')"
    ) == "0"


def test_the_map_can_hide_shapes_with_checkboxes(page, server, seeded):
    """一覧のチェックで、出すものを選べる。

    **外したものも一覧に残す**（消すと戻せない）。**外した数は凡例に出す**
    （黙って欠けた図を出さない）。カテゴリの印を押すとまとめて切り替わる。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.24, 0.30],
    ), ref=a.ref)
    store.save(EntryDraft(
        term=b.term, category=b.category, definition=b.definition,
        map="てすと図", pin=[0.62, 0.44],
    ), ref=b.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    assert page.locator("svg.rel-map .rel-map-pin").count() == 2

    page.uncheck("#mapLayers [data-ref=mapItem]:has-text('カムパネルラ') input")
    page.wait_for_timeout(300)
    assert page.locator("svg.rel-map .rel-map-pin").count() == 1
    # **外したものは一覧に残る**（消すと戻せない）
    assert page.locator("#mapLayers [data-ref=mapItem]").count() == 2
    assert "チェックを外した 1 語" in (page.text_content("#legend") or "")

    # カテゴリの印でまとめて切り替わる。**半端なときは「全部出す」に倒す**
    # （迷ったら出す側、が約束。ここを「全部外す」にすると、1 つ外しただけの
    # つもりで押した人が図を丸ごと失う）
    page.click("#mapLayers button.chip")
    page.wait_for_timeout(300)
    assert page.locator("svg.rel-map .rel-map-pin").count() == 2

    # 全部出ているときだけ、押すと全部外れる
    page.click("#mapLayers button.chip")
    page.wait_for_timeout(300)
    assert page.locator("svg.rel-map .rel-map-pin").count() == 0
    assert "すべてチェックが外れています" in (page.text_content("#legend") or "")

    # 戻せる（外したほうを覚えているので、開き直しても状態は残る）
    page.click("#mapLayers button.chip")
    page.wait_for_timeout(300)
    assert page.locator("svg.rel-map .rel-map-pin").count() == 2


def test_the_map_can_turn_the_names_off(page, server, seeded):
    """**名前は消せる。** AI に描かせた地図には地名が焼き込まれているのが普通。

    消しても情報は失われない —— 点も線も残り、乗せれば図の下の枠に名前が出る
    （だから「黙って欠けた図」にはならない）。消していることは凡例にも書く。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.24, 0.30],
    ), ref=a.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    assert page.locator("svg.rel-map .rel-map-plate").count() == 1

    page.uncheck("#mapLayers [data-ref=mapNames] input")
    page.wait_for_timeout(300)
    # 名前だけ消える。点は残る（＝情報を失っていない）
    assert page.locator("svg.rel-map .rel-map-plate").count() == 0
    assert page.locator("svg.rel-map .rel-map-point circle").count() == 1
    assert "名前は消しています" in (page.text_content("#legend") or "")

    page.check("#mapLayers [data-ref=mapNames] input")
    page.wait_for_timeout(300)
    assert page.locator("svg.rel-map .rel-map-plate").count() == 1


def _place_line(term: str, points: list[list[float]]) -> str:
    """その語を「てすと図」の上に線として置く。ref を返す。"""
    e = store.find_by_surface(term)[0]
    store.save(EntryDraft(
        term=e.term, category=e.category, definition=e.definition,
        map="てすと図", line=points,
    ), ref=e.ref)
    return e.ref


def _open_map(page, server):
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)


def test_vertices_can_be_added_and_removed(page, server, seeded):
    """**頂点を足す / 消す。** 足す口は線分の中点、消す口は乗せたときだけ出る ✕。

    **最小（線は 2 点）を割る削除は断る** —— 通すとサーバ側で形が丸ごと空になり、
    「1 つ消したら地図から消えた」になる。種別を勝手に落とさないのも同じ理由。
    """
    _put_test_map()
    _place_line("ジョバンニ", [[0.2, 0.3], [0.6, 0.3]])

    _open_map(page, server)
    page.check("#mapLayers [data-ref=mapEdit] input")
    page.locator("svg.rel-map .rel-map-handle").first.wait_for(timeout=10000)
    assert page.locator("svg.rel-map .rel-map-handle").count() == 2
    # 2 点の線には線分が 1 本 ＝ 足す口も 1 つ
    assert page.locator("svg.rel-map .rel-map-add").count() == 1

    page.click("svg.rel-map .rel-map-add")
    page.wait_for_function(
        "() => document.querySelectorAll('svg.rel-map .rel-map-handle').length === 3",
        timeout=10000,
    )

    # ✕ は乗せるまで出ない（常に出すと、掴もうとして消すことになる）
    vertex = page.locator("svg.rel-map .rel-map-vertex").first
    assert not vertex.locator(".rel-map-drop").is_visible()
    vertex.hover()
    vertex.locator(".rel-map-drop").click()
    page.wait_for_function(
        "() => document.querySelectorAll('svg.rel-map .rel-map-handle').length === 2",
        timeout=10000,
    )

    # **これ以上は断る**（キーボードからも同じ道を通る）
    page.locator("svg.rel-map .rel-map-handle").first.focus()
    page.keyboard.press("Delete")
    page.locator("#status.error").wait_for(timeout=10000)
    assert "2 点からです" in (page.text_content("#status") or "")
    assert page.locator("svg.rel-map .rel-map-handle").count() == 2


def test_the_kind_can_be_changed_and_undone(page, server, seeded):
    """**種別は人が宣言する。** 点 → 線 にすると足りない点が隣に足される
    （これが画面から線を作る道）。

    保存は即時なので、**戻す道を必ず出す** —— 消した頂点はこれが無いと
    取り返せない（→ docs/open-questions.md にあった宿題）。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.30, 0.40],
    ), ref=a.ref)

    _open_map(page, server)
    page.check("#mapLayers [data-ref=mapEdit] input")
    page.locator("#mapLayers [data-ref=mapKind]").first.wait_for(timeout=10000)
    assert page.locator("svg.rel-map .rel-map-point circle").count() == 1

    page.select_option("#mapLayers [data-ref=mapKind]", "line")
    # **`state="attached"` で待つ。** 真横・真縦に並んだ線は**外形の高さ（幅）が 0**
    # なので、「見えるまで」待つと真横の線で必ずタイムアウトする（`hitBand()` を
    # 敷いてある理由と同じ話。自動で足す点は斜めに置いてあるが、手で動かせば起きる）
    page.locator("svg.rel-map .rel-map-line polyline.rel-map-route").wait_for(
        state="attached", timeout=10000
    )
    page.wait_for_function(
        "() => document.querySelectorAll('svg.rel-map .rel-map-handle').length === 2",
        timeout=10000,
    )
    assert store.get(a.ref).line and not store.get(a.ref).pin

    # **戻せる**（何を戻すのかもボタンに書く）
    undo = page.locator("#mapLayers [data-ref=mapUndo]")
    undo.wait_for(timeout=10000)
    assert "ジョバンニ" in (undo.text_content() or "")
    undo.click()
    page.locator("svg.rel-map .rel-map-point circle").wait_for(timeout=10000)
    assert store.get(a.ref).pin == [0.30, 0.40] and not store.get(a.ref).line
    # 戻したら、戻す先はもう無い
    assert page.locator("#mapLayers [data-ref=mapUndo]").count() == 0


def test_the_entry_page_opens_the_map_at_that_word(page, server, seeded):
    """用語ページ →「🗺 地図で見る」→ 地図が開き、**その語が光る**。

    座標は用語のファイルに書くのに、**地図はそこから開けなかった**（置く動線も
    相関図の覆いの中にしかなかった）。`?mode=map` で見せ方まで名指しするので、
    **覚えている見せ方が段の図の人にも効く** —— 押しのけたことは注意書きに出す。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.24, 0.30],
    ), ref=a.ref)

    page.goto(f"{server}/glossary/登場人物/ジョバンニ")
    link = page.locator("[data-ref=mapLink]")
    link.wait_for(timeout=15000)
    link.click()

    page.locator("svg.rel-map").wait_for(timeout=15000)
    # **その語に目印が付く**（広い絵の隅にあると、開いても自分の語を見失う）
    lit = page.locator("svg.rel-map .rel-node.lit")
    lit.wait_for(timeout=10000)
    assert lit.get_attribute("data-ref") == a.ref
    # 覚えている見せ方を押しのけたことは黙らない
    assert "リンクの指定で地図" in (page.text_content("#notes") or "")


def test_the_map_folds_names_that_would_overlap(page, server, seeded):
    """**重なった名前は畳む**（地図は座標が与えられているので逃がす場所が無い）。

    重ねると重なった 2 つとも読めず、下の絵まで隠す。消してはいないので、
    畳んだ数を注意書きに出し、乗せれば出る。
    """
    _put_test_map()
    for term, spot in (("ジョバンニ", [0.40, 0.30]), ("カムパネルラ", [0.41, 0.30])):
        e = store.find_by_surface(term)[0]
        store.save(EntryDraft(
            term=e.term, category=e.category, definition=e.definition,
            map="てすと図", pin=spot,
        ), ref=e.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)

    # 板は 2 枚とも DOM にある（消していない）が、片方は畳まれている
    assert page.locator("svg.rel-map .rel-map-plate").count() == 2
    assert page.locator("svg.rel-map .rel-map-name.tucked").count() == 1
    assert "重なって置けない名前 1 個" in (page.text_content("#legend") or "")

    # **乗せれば出る**（畳んだものが読めなくなっていないこと）
    tucked = page.locator("svg.rel-map .rel-node:has(.rel-map-name.tucked)")
    assert tucked.evaluate(
        "(node) => { node.classList.add('lit');"
        " return getComputedStyle(node.querySelector('.rel-map-name')).display; }"
    ) != "none"


def test_a_map_image_can_be_uploaded_and_deleted(page, server, seeded):
    """絵をブラウザから入れて消せる。

    **絵が 1 枚も無いと段の図に落ちる**ので、そのときも「🖼 絵」は出ていなければ
    ならない —— 隠すと**最初の 1 枚を入れる道が無くなる**（鶏と卵）。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="あたらしい図", pin=[0.5, 0.4],
    ), ref=a.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    # 絵が無いので段の図に落ちる。**それでもボタンは出ている**
    page.locator("svg.rel-graph:not(.rel-map)").wait_for(timeout=10000)
    assert not page.locator("#mapEdit").is_hidden()

    page.click("#mapEdit")
    page.locator("#mapDialog[open]").wait_for(timeout=10000)
    page.fill("#mapDialog [data-ref=name]", "あたらしい図")
    page.set_input_files(
        "#mapDialog [data-ref=file]",
        files=[{"name": "m.svg", "mimeType": "image/svg+xml", "buffer": TEST_MAP.encode()}],
    )
    page.click("#mapDialog [data-ref=save]")
    page.locator("#mapDialog .cat-row .map-thumb").first.wait_for(timeout=10000)

    # 閉じると図を取り直し、地図が出せるようになる
    page.click("#mapDialog [data-ref=close]")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    assert page.locator("svg.rel-map .rel-map-pin").count() == 1

    # 消すと出せなくなる（**辞書は消えない**ので、段の図に落ちるだけ）
    page.once("dialog", lambda d: d.accept())
    page.click("#mapEdit")
    page.locator("#mapDialog[open]").wait_for(timeout=10000)
    page.click("#mapDialog .cat-row button.ghost")
    page.locator("#mapDialog .empty").wait_for(timeout=10000)
    page.click("#mapDialog [data-ref=close]")
    page.locator("svg.rel-graph:not(.rel-map)").wait_for(timeout=10000)
    assert len(store.find_by_surface("ジョバンニ")) == 1


def test_a_shape_can_be_dragged_and_placed_on_the_map(page, server, seeded):
    """地図の上で掴んで動かす / 置く。**地図だけの例外**（→ docs/design-notes.md）。

    相関図で「掴んで動かす」を捨てた理由は「座標を書く場所が無い」だったが、
    地図はまさにその場所を作る図なので、ここでは正当化される。
    **他の見せ方へ広げないこと。**
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.24, 0.30],
    ), ref=a.ref)
    # **絵の名前だけ書いて形が無い語 = 置き待ち**（分類ではなく、書いてある意思表示）
    store.save(EntryDraft(
        term=b.term, category=b.category, definition=b.definition, map="てすと図",
    ), ref=b.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-map").wait_for(timeout=10000)
    # 閲覧中は掴めない（うっかり動かさない）
    assert page.locator("svg.rel-map .rel-map-handle").count() == 0
    assert "置きたいと書いてある語が 1 語" in (page.text_content("#legend") or "")

    page.check("#mapLayers [data-ref=mapEdit] input")
    page.locator("svg.rel-map .rel-map-handle").first.wait_for(timeout=10000)
    handle = page.locator("svg.rel-map .rel-map-handle").first
    box = handle.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + 120, box["y"] + 60, steps=8)
    page.mouse.up()
    page.wait_for_timeout(600)
    moved = store.get(a.ref).pin
    assert moved != [0.24, 0.30] and len(moved) == 2, moved

    # まだ置いていない語を選び、絵の上を押すと置ける
    page.click("#mapLayers [data-ref=mapPending]")
    page.wait_for_timeout(300)
    stage = page.locator("svg.rel-map").bounding_box()
    page.mouse.click(stage["x"] + stage["width"] * 0.6, stage["y"] + stage["height"] * 0.5)
    page.wait_for_timeout(600)
    assert len(store.get(b.ref).pin) == 2, store.get(b.ref).pin


def test_the_map_mode_says_so_when_nothing_has_coordinates(page, server, seeded):
    """座標が 1 つも無ければ段の図に落とすが、**黙って差し替えない。**

    時系列が `?doc=` 無しで選べないときと同じ扱い（注意書きを出し、覚えている
    選択のほうは書き換えない）。
    """
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "map")
    page.locator("svg.rel-graph:not(.rel-map)").wait_for(timeout=10000)
    assert "地図に置ける語" in (page.text_content("#notes") or "")
    assert page.locator("#mode").input_value() == "map"


def test_the_graph_explains_what_you_point_at_in_a_fixed_box(page, server, seeded):
    """図の下の枠。**高さは常に同じ**で、乗せたもの／焦点が当たったものを説明する。

    ブラウザの吹き出し（`title`）は**キーボードの焦点では出ない**うえ、遅れて
    出て消える。図の中では一言を切ったり畳んだりしているので、**全文が読める
    場所がここしかない**。3 つの見せ方すべてで効くこと。

    高さが変わると下の凡例まで動いて読みにくい ——「常に空けておく」が肝。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"}],
        ),
        ref=a.ref,
    )
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)

    detail = page.locator("#detail")
    hint = detail.text_content()
    assert "乗せる" in (hint or ""), "空のときの案内が無い"
    empty_height = detail.bounding_box()["height"]

    over = (
        "(sel) => { const n = document.querySelector(sel);"
        " n.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));"
        " return document.getElementById('detail').textContent; }"
    )
    for mode in ["layered", "fabric", "matrix"]:
        page.select_option("#mode", mode)
        page.wait_for_timeout(200)
        said = page.evaluate(over, "svg .rel-edge-group")
        assert "ジョバンニ" in said and "親友" in said and "相互" in said, f"{mode}: {said}"
        assert "カムパネルラ" in page.evaluate(over, "svg .rel-node")
        # **高さは変わらない**（下の凡例ごと動かさない）
        assert abs(detail.bounding_box()["height"] - empty_height) < 1, mode

    # **キーボードの焦点でも出る**（ブラウザの吹き出しはここが出ない）
    page.select_option("#mode", "layered")
    page.wait_for_timeout(200)
    page.locator("svg .rel-edge-group").first.focus()
    page.wait_for_timeout(150)
    assert "親友" in (detail.text_content() or "")
    # 焦点が外れたら案内文に戻る
    page.locator("#mode").focus()
    page.wait_for_timeout(150)
    assert detail.text_content() == hint


def test_the_matrix_mode_shows_which_pairs_have_no_relation(page, server, seeded):
    """行列は**書いていない組が見える**唯一の見せ方。

    段の図も交差しない図も、書かれた関係しか描かない —— 「まだ書いていない組」は
    絵に出ないので数えられない。行列は空きマスとして残る。ここが緩むと
    （埋まったマスだけ置く実装に戻すと）、この見せ方を足した意味が消える。

    **相互は対角の両側を埋める。** 関係はファイルには片側にしか書かれないが、
    片側だけ埋めると一方的に見える —— 画面には「両側が埋まっていれば相互」と
    書いてあるので、そちらに合わせる。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    zanelli = store.save(EntryDraft(term="ザネリ", category="登場人物", definition="級友。"))
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[
                {"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"},   # 相互
                {"to": zanelli.ref, "label": "同級生"},                          # 一方的
            ],
        ),
        ref=a.ref,
    )
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "matrix")
    page.locator("svg.rel-matrix").wait_for(timeout=10000)
    page.wait_for_timeout(200)

    # 線は 1 本も引かない（交差という概念が無い）
    assert page.locator("svg.rel-matrix .rel-edge").count() == 0
    # 3 語ぶんの格子を**空きマスごと**描く（横と縦で (3+1) 本ずつ）
    assert page.locator("svg.rel-matrix .mx-grid line").count() == 8
    # 相互は 2 マス、一方的は 1 マス
    assert sorted(page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-matrix .rel-edge-group')]"
        ".map((g) => g.querySelectorAll('rect').length)"
    )) == [1, 2]

    # マスを押せば同じ編集ダイアログが開く（相互は 2 マスあり、外接矩形の中心は
    # その間の空きに来るので、マスそのものを押す）
    page.locator("svg.rel-matrix .rel-edge-group rect").first.click()
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)
    page.keyboard.press("Escape")

    # 語に乗せると、その語の行と列が十字に残る（塗るのは乗せた語だけ）
    page.locator("svg.rel-matrix .rel-node a text", has_text="ジョバンニ").first.hover()
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.querySelectorAll('.rel-node.here').length") == 1


def _neighbourhood():
    """3 つ先まで繋がった鎖を作る（2 つ先で切れることを見るため）。"""
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    zanelli = store.save(EntryDraft(term="ザネリ", category="登場人物", definition="級友。"))
    teacher = store.save(EntryDraft(term="先生", category="登場人物", definition="教師。"))
    far = store.save(EntryDraft(term="遠い人", category="登場人物", definition="ザネリの知り合い。"))
    edge = store.save(EntryDraft(term="果ての人", category="登場人物", definition="さらに先。"))
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[
                {"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"},
                {"to": zanelli.ref, "label": "同級生", "rank": "対等"},
                {"to": teacher.ref, "label": "教わる相手", "rank": "上"},
            ],
        ),
        ref=a.ref,
    )
    store.save(
        EntryDraft(
            term=zanelli.term, category=zanelli.category, definition=zanelli.definition,
            relations=[{"to": far.ref, "label": "知り合い", "rank": "対等"}],
        ),
        ref=zanelli.ref,
    )
    store.save(
        EntryDraft(
            term=far.term, category=far.category, definition=far.definition,
            relations=[{"to": edge.ref, "label": "知り合い", "rank": "対等"}],
        ),
        ref=far.ref,
    )
    return a


def test_the_ego_mode_shows_two_steps_around_one_word(page, server, seeded):
    """1 語を中心にした図。**規模に依らない**唯一の見せ方。

    他の見せ方はどれも辞書（またはその文書）を一度に出すので、語が増えれば必ず
    苦しくなる。中心を決めれば絵の大きさはその語の近所の広さで決まる。
    **出していないものは必ず数える** —— この図だけを見て「関係はこれで全部」と
    読まれないように。
    """
    _neighbourhood()
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "ego")
    page.locator("svg.rel-ego").wait_for(timeout=10000)
    page.wait_for_timeout(200)

    # 中心は指されていなければ「いちばん多く繋がっている語」
    assert page.locator("svg.rel-ego .ego-center text").first.text_content() == "ジョバンニ"
    drawn = page.locator("svg.rel-ego").text_content() or ""
    assert "遠い人" in drawn                       # 2 つ先までは出す
    assert "果ての人" not in drawn                 # 3 つ先は出さない
    # 黙って欠けさせない（出していない語は数えて凡例に出す）
    assert "ほか 1 語" in (page.text_content("#legend") or "")

    # **まわりの語を押すと中心が移る**（サーバへは行き直さない）
    page.locator("svg.rel-ego .ego-ring1", has_text="ザネリ").first.click()
    page.wait_for_function(
        "() => document.querySelector('svg.rel-ego .ego-center text')?.textContent === 'ザネリ'",
        timeout=10000,
    )
    # 中心が移れば、さっき遠すぎた語が近所に入る
    assert "果ての人" in (page.locator("svg.rel-ego").text_content() or "")

    # 線を押せば同じ編集ダイアログが開く（見せ方が変わっても直し方は 1 つ）
    page.locator("svg.rel-ego .rel-edge-group").first.click()
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)
    page.keyboard.press("Escape")


def test_the_ego_mode_takes_its_center_from_the_url(page, server, seeded):
    """用語ページの「相関図で見る」は `?ref=` を付けてくる（その語が真ん中に来る）。"""
    _neighbourhood()
    zanelli = store.find_by_surface("ザネリ")[0]
    page.goto(f"{server}/graph?ref={zanelli.ref}")
    page.locator("svg.rel-ego").wait_for(timeout=15000)
    assert page.locator("svg.rel-ego .ego-center text").first.text_content() == "ザネリ"


def test_the_timeline_orders_relations_by_where_they_become_readable(page, server, seeded):
    """時系列は**関係が読めるようになる順**に並べる。

    位置は「両端が出そろうところ」で、毎回その場で計算する（保存しない）。
    段の図・交差しない図・行列はどれも辞書を平らに出すので、**どの関係が先に
    読めるようになるのかは絵に出ない** —— そこがこの見せ方の役目。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    zanelli = store.save(EntryDraft(term="ザネリ", category="登場人物", definition="級友。"))
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[
                {"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"},
                {"to": zanelli.ref, "label": "同級生", "rank": "対等"},
            ],
        ),
        ref=a.ref,
    )
    # ザネリだけ後の章に出す（＝その関係はそこまで読めない）
    (config.content_dir() / "章.md").write_text(
        "# 一\n\nジョバンニは活版所にいた。カムパネルラは黙っていた。\n\n# 二\n\nザネリが囃した。\n",
        encoding="utf-8",
    )

    page.goto(f"{server}/graph?doc=章.md")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "timeline")
    page.locator("svg.rel-timeline").wait_for(timeout=10000)
    page.wait_for_timeout(200)

    # 帯の見出しは上から順。両方が出そろう位置なので、ザネリ行きは後の帯へ
    assert page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-timeline .tl-head text')]"
        ".map((t) => t.textContent)"
    ) == ["L.3", "L.7"]
    # 行そのものも同じ順（上が先）
    assert page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-timeline .rel-edge-label')]"
        ".sort((p, q) => Number(p.getAttribute('y')) - Number(q.getAttribute('y')))"
        ".map((t) => t.textContent)"
    ) == ["親友", "同級生"]

    # 線を押せば同じ編集ダイアログが開く（見せ方が変わっても直し方は 1 つ）
    page.click("svg.rel-timeline .rel-edge-group")
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)
    page.keyboard.press("Escape")


def test_the_timeline_is_only_offered_when_a_document_is_open(page, server, seeded):
    """辞書全体では時系列を出せない（読むものが決まっていないと定義できない）。

    **覚えていた見せ方を黙って別のものに差し替えない。** 何が起きたのかを
    注意書きに出し、覚えている選択のほうは書き換えない（文書を開いて戻ったら
    また時系列で出す）。
    """
    page.goto(f"{server}/graph")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    assert page.locator('#mode option[value="timeline"]').is_disabled()

    page.evaluate("() => localStorage.setItem('glosspop.graphMode', 'timeline')")
    page.reload()
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    assert page.locator("#mode").input_value() == "layered"
    assert "時系列は文書を開いているとき" in page.locator("#notes").inner_text()


def test_the_crossing_free_mode_sets_its_words_vertically(page, server, seeded):
    """列の上の一言は**縦書き**（1 字ずつ立てて積む）。

    `writing-mode: vertical-rl` を使わないのは、**`⇄` が `⇅` に回される**から
    （`text-orientation: upright` を付けても Chrome では回った）。この辞書では
    `⇄` が「相互」で、上下は `▲▼` と決めてあるので、回った矢印は別の意味に読める。
    長音符や括弧のほうは逆に**寝かせる**（立てたままだと横倒しに見える）。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "パートナー（仮）", "back": "相棒", "rank": "対等"}],
        ),
        ref=a.ref,
    )
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    page.select_option("#mode", "fabric")
    page.locator("svg.rel-fabric .rel-edge-label").wait_for(timeout=10000)

    stacked = page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-fabric .rel-edge-label tspan')]"
        ".map((t) => ({ ch: t.textContent, y: Number(t.getAttribute('y')),"
        " laid: t.hasAttribute('rotate') }))"
    )
    # 1 字ずつ、上から下へ積まれている（横に流れていない）
    assert [s["ch"] for s in stacked] == list("パートナー（仮）⇄相棒")
    assert [s["y"] for s in stacked] == sorted(s["y"] for s in stacked)
    assert len({s["y"] for s in stacked}) == len(stacked)
    # ⇄ は立てたまま、長音符と括弧は寝かせる
    laid = {s["ch"] for s in stacked if s["laid"]}
    assert laid == {"ー", "（", "）"}, laid


def test_the_graph_dims_everything_but_the_word_you_point_at(page, server, seeded):
    """1 つの語に乗せると、その語の関係だけが濃く出る。

    交差はどう並べても消せない（→ docs/design-notes.md）。密なところは
    「一度に全部を読ませない」のが唯一きく手なので、ここが効かなくなると
    語が増えたときに読む手立てが無くなる。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"}],
        ),
        ref=a.ref,
    )
    # 関係の無い語。乗せていない間は同じ濃さで、乗せると薄くなる側
    store.save(EntryDraft(term="ザネリ", category="登場人物", definition="関係は書かない。"))

    page.goto(f"{server}/graph?category=登場人物")
    page.wait_for_function(
        "() => document.querySelectorAll('svg.rel-graph .rel-node').length === 3",
        timeout=15000,
    )
    lit = "() => document.querySelectorAll('svg.rel-graph .lit').length"
    assert page.evaluate(lit) == 0

    page.locator("svg.rel-graph .rel-node", has_text="ジョバンニ").first.hover()
    page.wait_for_timeout(200)
    assert page.evaluate(
        "() => document.querySelector('svg.rel-graph').classList.contains('focusing')"
    )
    # 自分・相手・その 1 本（線と一言）が濃く、関係の無い語は濃くならない
    assert set(page.evaluate(
        "() => [...document.querySelectorAll('svg.rel-graph .rel-node.lit text')]"
        ".map((t) => t.textContent)"
    )) == {"ジョバンニ", "カムパネルラ"}
    assert page.evaluate("() => document.querySelectorAll('.rel-edge-group.lit').length") == 1

    page.mouse.move(0, 0)
    page.wait_for_timeout(200)
    assert page.evaluate(lit) == 0, "離れても濃いまま"


def test_the_graph_can_be_panned_and_zoomed(page, server, seeded):
    """図はドラッグで動かし、ホイールで拡大縮小できる。

    **掴みを `pointerdown` で捕まえないこと**の見張りでもある。捕まえると以後の
    ポインタ事象が入れ物へ付け替えられ、**線を押しても編集ダイアログが開かなくなる**
    （HTML も JS も正しいので、画面を開くまで気付けない壊れ方）。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "親友", "back": "親友", "rank": "対等"}],
        ),
        ref=a.ref,
    )
    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    box_of = "() => document.querySelector('svg.rel-graph').getAttribute('viewBox')"
    read = lambda: [float(v) for v in page.evaluate(box_of).split()]  # noqa: E731

    fit = read()
    canvas = page.locator("#canvas").bounding_box()
    cx = canvas["x"] + canvas["width"] / 2
    cy = canvas["y"] + canvas["height"] / 2

    page.mouse.move(cx, cy)
    page.mouse.wheel(0, -400)
    page.wait_for_timeout(150)
    zoomed = read()
    assert zoomed[2] < fit[2], "ホイールで拡大できない"

    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx - 120, cy - 60, steps=8)
    page.mouse.up()
    page.wait_for_timeout(150)
    panned = read()
    assert panned[0] > zoomed[0] and panned[1] > zoomed[1], "ドラッグで動かない"
    assert abs(panned[2] - zoomed[2]) < 0.01, "動かしただけで拡大率が変わった"
    # 掴んで動かした直後のクリックは飲む（線の上で放しても編集は開かない）
    assert page.locator("#edgeDialog[open]").count() == 0

    page.click("#zoomFit")
    page.wait_for_timeout(150)
    assert [round(v, 1) for v in read()] == [round(v, 1) for v in fit], "全体に戻らない"

    # **縮めても線は押せる**（当たり判定だけは縮めていない）
    for _ in range(3):
        page.click("#zoomOut")
    page.wait_for_timeout(150)
    page.click("svg.rel-graph .rel-edge-group")
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)


def test_the_viewer_opens_a_graph_of_just_this_document(page, server, seeded):
    """ビューア →「この文書の相関図」→ **その文書に出てくる語だけ**の図。

    以前は相関図が辞書全体しか出せず、読んでいるものに辿り着けなかった
    （→ docs/design-notes.md）。**何を出している図なのかを画面に書くこと**も
    あわせて見る —— 書かないと、全体の図を開いている文書の図だと思われる。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    # この文書には出てこない語。関係だけ張っておく（図から落ちるのが正しい）
    zanelli = store.save(EntryDraft(term="ザネリ", category="登場人物", definition="級友。"))
    store.save(
        EntryDraft(
            term=a.term, category=a.category, summary=a.summary, definition=a.definition,
            relations=[{"to": b.ref, "label": "親友", "back": "親友"},
                       {"to": zanelli.ref, "label": "同級生"}],
        ),
        ref=a.ref,
    )

    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
    page.click("#docGraph")

    page.locator("svg.rel-graph").wait_for(timeout=SETTLE_MS)
    assert "銀河.md" in page.locator("#scopeNote").inner_text()
    nodes = page.locator("svg.rel-graph .rel-node")
    assert nodes.count() == 2                      # ザネリは出てこないので出ない
    assert "ザネリ" not in (page.locator("svg.rel-graph").text_content() or "")
    # 落とした辺は黙って消さない
    assert "1 本伏せています" in page.locator("#notes").inner_text()

    # 辞書全体に戻せること（戻したらザネリも出る）
    page.click("#scopeAll")
    page.wait_for_function(
        "() => document.querySelectorAll('svg.rel-graph .rel-node').length === 3",
        timeout=SETTLE_MS,
    )
    assert "辞書全体" in page.locator("#scopeNote").inner_text()


def test_the_read_aloud_button_matches_what_the_browser_offers(page, server, seeded):
    """読み上げ。**音声が 0 件ならボタンごと出さず、あるなら押せる。**

    以前はボタンを出しておいて、押してから「この環境には読み上げに使える音声が
    ありません」と断っていた（できないことを毎回試させていた）。

    **環境を前提にしない。** 手元の Chrome に音声があるかは PC によって違い、
    playwright の起動では `--disable-background-networking` が効いてオンラインの
    音声一覧すら来ない（実測: 本物の起動なら 23 件、playwright 経由だと 0 件）。
    そこで「ブラウザが何件返すか」をその場で数え、**ボタンの出方がそれと
    合っているか**だけを見る —— これならどちらの環境でも意味を持つ。
    """
    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)

    # 一覧は非同期に埋まる。speech.js と同じだけ待ってから数える
    counts = page.evaluate("""() => new Promise((resolve) => {
      const dump = () => {
        const all = speechSynthesis.getVoices();
        return {all: all.length, local: all.filter((v) => v.localService !== false).length};
      };
      if (dump().all) return resolve(dump());
      setTimeout(() => resolve(dump()), 2000);
      speechSynthesis.addEventListener("voiceschanged", () => resolve(dump()), {once: true});
    })""")
    speak = page.locator("#speak")
    placeholder = page.locator("#speakVoice option[value='']")

    if not counts["all"]:
        assert speak.is_hidden(), "音声が 1 つも無いのに読み上げボタンが出ている"
        return

    speak.wait_for(state="visible", timeout=SETTLE_MS)
    if counts["local"]:
        # ローカルの音声があるなら、それが既定に選ばれている（今までどおり押せば喋る）。
        # **ここでは押さない** —— 通ったマシンで実際に音が出るのは行儀が悪い
        assert placeholder.count() == 0, "ローカルの音声があるのに選ばれていない"
        return

    # オンラインの音声しかない環境。**押しても喋らず、選ばせる。**
    # 既定に任せると本文がその提供元へ送られる
    speak.click()
    page.locator("#speechBar").wait_for(state="visible", timeout=SETTLE_MS)
    assert placeholder.count() == 1, "選ばれていない状態が一覧に出ていない"
    assert "🌐" in (page.locator("#speakVoice").text_content() or ""), "オンラインの印が無い"


def test_the_viewer_comes_back_to_what_you_were_reading(page, server, seeded):
    """寄り道して戻っても、開いていた本文が消えないこと。

    相関図へ行って戻ると案内文に戻ってしまい、**読んでいたものを開き直す**
    ところからやり直しになっていた。担保は 2 つあり、**どちらも見る**:
    図の「ビューア」が同じ文書を指すことと、素の `/` でも開き直すこと。
    """
    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)

    # **ページを離れていない**ことと、**本文を描き直していない**ことの目印
    page.evaluate("() => { window.__alive = true; }")
    renders = page.evaluate(
        "() => performance.getEntriesByType('resource')"
        ".filter(r => r.name.includes('/api/render')).length"
    )

    page.click("#docGraph")
    page.locator("svg.rel-graph").wait_for(timeout=SETTLE_MS)
    page.click('.topnav a:has-text("ビューア")')
    page.locator("svg.rel-graph").wait_for(state="detached", timeout=SETTLE_MS)

    assert "銀河.md" in page.locator("#docMeta").inner_text()
    assert page.evaluate("() => window.__alive") is True, "ページを離れている（重なっていない）"
    assert page.evaluate(
        "() => performance.getEntriesByType('resource')"
        ".filter(r => r.name.includes('/api/render')).length"
    ) == renders, "戻るのに本文を描き直している"

    # 素の `/` でも、最後に読んでいたものを開き直す（辞書側から戻る道）
    page.goto(f"{server}/")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
    assert "銀河.md" in page.locator("#docMeta").inner_text()

    # 貼り付けに切り替えたら忘れる（読み直す道が無いものを覚えると嘘になる）
    open_other_source(page)
    page.fill("#paste", "ただのメモ。")
    page.click("#showPaste")
    page.locator("#doc:has-text('ただのメモ')").wait_for(timeout=SETTLE_MS)
    page.goto(f"{server}/")
    page.locator("#doc h1:has-text('GlossPop')").wait_for(timeout=SETTLE_MS)
    assert page.locator("#docHead").is_hidden()


def test_everything_opens_over_the_viewer_without_leaving_it(page, server, seeded):
    """辞書・用語・相関図・点検は**ビューアの上に重ねる**。

    ページとして開き直すと、戻るたびに本文を取り直して描き直すことになる
    （実測 149 ms / 39,000 字。長編ではその数倍。→ docs/design-notes.md）。
    見るのは「ページを離れていないこと」で、`window.__alive` が残っていれば
    重なっている（離れれば JS の世界ごと作り直される）。
    """
    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
    page.evaluate("() => { window.__alive = true; }")

    for nav, ready in [
        ('.topnav a:has-text("辞書")', ".overlay .card"),
        ('.topnav a:has-text("相関図")', ".overlay svg.rel-graph"),
        ('.overlay a:has-text("点検")', ".overlay .entry-head h1"),
    ]:
        page.click(nav)
        page.locator(ready).first.wait_for(timeout=SETTLE_MS)
        assert page.evaluate("() => window.__alive") is True, f"{nav} でページを離れている"

    # 用語ページも重なる（吹き出しの「辞書ページを開く →」から）
    page.click("[data-ref=close]")
    page.locator("#doc a.gloss-link").first.click()
    page.locator(".gloss-pop .pop-foot a").wait_for(timeout=SETTLE_MS)
    page.click(".gloss-pop .pop-foot a")
    page.locator(".overlay .entry-head h1").wait_for(timeout=SETTLE_MS)
    assert page.evaluate("() => window.__alive") is True

    # Esc で閉じて読書に戻る
    page.keyboard.press("Escape")
    page.locator(".overlay .entry-head h1").wait_for(state="hidden", timeout=SETTLE_MS)
    assert "銀河.md" in page.locator("#docMeta").inner_text()


def test_a_change_made_in_the_overlay_reaches_the_text_underneath(page, server, isolated_dirs):
    """重ねた側で登録したら、閉じたときに下の本文がリンクになること。

    **変わったときだけ描き直す**という約束（`dictionaryRevision()`）の裏側。
    描き直しを止めすぎると、登録したのに本文が古いまま＝「登録できていない」
    ように見える。逆に毎回描き直すと重ねた意味が無くなるので、両方見る。
    """
    (config.content_dir() / "銀河.md").write_text(
        "ジョバンニは活版所で働いていた。\n", encoding="utf-8"
    )
    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("#doc:has-text('活版所')").wait_for(timeout=SETTLE_MS)
    assert page.locator("#doc a.gloss-link").count() == 0
    page.evaluate("() => { window.__alive = true; }")

    page.click('.topnav a:has-text("辞書")')
    page.locator(".overlay #add").wait_for(timeout=SETTLE_MS)
    page.click(".overlay #add")
    page.locator("dialog.sheet[open] [data-ref=term]").wait_for(timeout=SETTLE_MS)
    page.fill("dialog.sheet[open] [data-ref=term]", "活版所")
    page.select_option("dialog.sheet[open] [data-ref=category]", "/new")   # ＋ 新しいカテゴリ
    page.fill("dialog.sheet[open] [data-ref=newCategory]", "場所")
    page.fill("dialog.sheet[open] [data-ref=definition]", "活字を組む仕事場。")
    page.click("dialog.sheet[open] [data-ref=save]")
    page.locator(".overlay .card").first.wait_for(timeout=SETTLE_MS)

    page.click(".overlay [data-ref=close]")
    # 閉じたら本文が描き直され、いま登録した語がリンクになっている
    page.locator("#doc a.gloss-link:has-text('活版所')").wait_for(timeout=SETTLE_MS)
    assert page.evaluate("() => window.__alive") is True, "ページを離れている（重なっていない）"


def test_the_entry_page_finds_where_the_term_appears_and_takes_an_example(page, server, seeded):
    """用語ページ →「出てくる文書」→ その文を使用例に足す、まで通す。

    開くたびに全文書を読ませないよう、`<details>` を開いた時点で初めて探す。
    畳んだままなら読みに行かないことも見る。
    """
    page.goto(f"{server}/glossary/登場人物/ジョバンニ")
    page.locator(".appearances-box").wait_for(timeout=15000)
    # 畳んでいる間は読みに行かない（用語ページの表示を重くしない）
    assert page.locator(".appearance-file").count() == 0

    page.click(".appearances-box > summary")
    page.locator(".appearance-file").first.wait_for(timeout=15000)
    # ファイル名はそのまま出す（小見出し用の uppercase を当てると 銀河.MD になる）
    assert "銀河.md" in page.locator(".appearances").inner_text()
    assert "1 件" in page.locator(".appearance-name").inner_text()

    page.click(".appearance-file button:has-text('使用例に足す')")
    page.locator("text=使用例").first.wait_for(timeout=15000)
    page.wait_for_function(
        "!!document.querySelector('.entry-section .doc')?.textContent.includes('活版所')",
        timeout=15000,
    )
    entry = store.get("登場人物/ジョバンニ")
    # 抜粋ではなく文の切れ目まで採る（「…」つきの半端な文を溜めない）
    assert entry.examples == ["ジョバンニは活版所で働いていた。"]


def test_two_entries_can_be_merged_from_the_entry_page(page, server, isolated_dirs):
    """割れてしまった同じものを、確認画面を通して 1 つにまとめる。

    見るのは「本当にまとまったか」だけでなく、**畳めない項目を人が選べたか**と、
    **消える側の呼び方が残ったか**。別名に回らないと、本文でその表記がリンクに
    ならず「まとめたのに片方だけ引けない」になる。
    """
    store.save(EntryDraft(term="主人", category="登場人物",
                          summary="猫の飼い主。", definition="飼い主の話。"))
    store.save(EntryDraft(term="苦沙弥先生", category="登場人物",
                          summary="中学校の英語教師。", definition="教師の話。"))
    doc = config.content_dir() / "猫.md"
    doc.write_text("# 一\n\n苦沙弥先生は書斎にいた。\n", encoding="utf-8")

    page.goto(f"{server}/glossary/登場人物/主人")
    page.locator(".entry-actions").wait_for(timeout=SETTLE_MS)
    page.click("button:has-text('まとめる')")
    # **候補は自動で挙げない。** 同じ表記のものだけ先に出し、あとは自分で探す
    # （「同じ人物かもしれない」を機械で判定すると、正常なカテゴリ違いの同名を
    #   大量に挙げて誰も読まなくなる）
    page.locator("dialog.sheet[open] input[aria-label='まとめる相手を探す']").wait_for(
        timeout=SETTLE_MS
    )
    assert "候補がありません" in page.locator("dialog.sheet[open] .body").inner_text()
    page.fill("dialog.sheet[open] input[aria-label='まとめる相手を探す']", "苦沙弥")
    page.locator("dialog.sheet[open] .merge-cand").first.wait_for(timeout=SETTLE_MS)
    page.click("dialog.sheet[open] .merge-cand:has-text('苦沙弥先生')")

    # 衝突した項目が並び、既定は残す側
    page.locator("dialog.sheet[open] .merge-conflict").first.wait_for(timeout=SETTLE_MS)
    body = page.locator("dialog.sheet[open] .body").inner_text()
    assert "猫の飼い主。" in body and "中学校の英語教師。" in body

    # 要約だけ消える側を選ぶ（本文は触らない＝残す側のまま）
    page.click("dialog.sheet[open] .merge-choice:has-text('中学校の英語教師。')")
    page.click("dialog.sheet[open] button:has-text('まとめる')")

    page.wait_for_function(
        "!!document.querySelector('.aliases')?.textContent.includes('苦沙弥先生')",
        timeout=SETTLE_MS,
    )
    merged = store.get("登場人物/主人")
    assert merged.summary == "中学校の英語教師。"       # 選んだほう
    assert merged.definition == "飼い主の話。"          # 触らなければ残す側
    assert "苦沙弥先生" in merged.aliases
    assert store.get("登場人物/苦沙弥先生") is None

    # **消える側の呼び方で本文がリンクになること。** ここが切れると意味がない
    page.goto(f"{server}/?open=%E7%8C%AB.md")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
    assert page.locator("a.gloss-link").first.text_content() == "苦沙弥先生"


def test_a_relation_can_be_edited_from_the_graph(page, server, seeded):
    """図の線を押してその場で直せること（辞書ページへ渡り歩かせない）。

    線は細いので、透明な太い当たり判定を重ねている。これが無いと押せず、
    HTML も JS も正しいのに「押しても何も起きない」になる。
    """
    store.save(EntryDraft(
        term="ジョバンニ", category="登場人物", summary="活版所で働く少年。",
        definition="主人公。", relations=[{"to": "カムパネルラ", "label": "親友"}],
    ), ref="登場人物/ジョバンニ")

    page.goto(f"{server}/graph")
    page.locator("svg.rel-graph .rel-edge-group").wait_for(timeout=15000)
    # **辺のラベルだけを見る。** SVG 全体の textContent には、ノードの <title> に
    # 入れた要約（「ジョバンニの級友。」）まで混ざるので、待ちが即座に成立してしまう
    # SVG の <text> は HTMLElement ではないので inner_text() は使えない
    assert page.locator("svg.rel-graph .rel-edge-label").text_content() == "親友"

    page.click("svg.rel-graph .rel-edge-group")
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)
    assert page.eval_on_selector("#edgeDialog [data-ref=to]", "n => n.value") == "カムパネルラ"
    assert "ジョバンニ → カムパネルラ" in page.locator("#edgeDialog [data-ref=who]").inner_text()

    page.fill("#edgeDialog [data-ref=label]", "級友")
    page.select_option("#edgeDialog [data-ref=rank]", "対等")
    page.click("#edgeDialog [data-ref=save]")

    page.wait_for_function(
        "document.querySelector('svg.rel-graph .rel-edge-label')?.textContent === '級友'",
        timeout=15000,
    )
    entry = store.get("登場人物/ジョバンニ")
    assert [(r.label, r.rank) for r in entry.relations] == [("級友", "対等")]


def test_a_relation_can_be_deleted_from_the_graph(page, server, seeded):
    store.save(EntryDraft(
        term="ジョバンニ", category="登場人物", summary="活版所で働く少年。",
        definition="主人公。", relations=[{"to": "カムパネルラ", "label": "親友"}],
    ), ref="登場人物/ジョバンニ")

    page.goto(f"{server}/graph")
    page.locator("svg.rel-graph .rel-edge-group").wait_for(timeout=15000)
    page.click("svg.rel-graph .rel-edge-group")
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)

    page.on("dialog", lambda d: d.accept())      # confirm() を通す
    page.click("#edgeDialog [data-ref=remove]")

    page.wait_for_function(
        "document.querySelectorAll('svg.rel-graph .rel-edge-group').length === 0",
        timeout=15000,
    )
    assert store.get("登場人物/ジョバンニ").relations == []


def test_a_stale_graph_refuses_to_write(page, server, seeded):
    """図を開いたあとに関係が変わっていたら、書かずに読み込み直させること。

    番号は**図を描いた時点**のものなので、そのまま書くと**別の関係を書き換える**。
    黙って壊れる形なので、書く直前に行き先を確かめている。
    """
    store.save(EntryDraft(
        term="ジョバンニ", category="登場人物", summary="活版所で働く少年。", definition="主人公。",
        relations=[{"to": "カムパネルラ", "label": "親友"}, {"to": "ザネリ", "label": "同級生"}],
    ), ref="登場人物/ジョバンニ")

    page.goto(f"{server}/graph")
    page.locator("svg.rel-graph .rel-edge-group").first.wait_for(timeout=15000)
    page.locator("svg.rel-graph .rel-edge-group").first.click()
    page.locator("#edgeDialog[open]").wait_for(timeout=10000)

    # 図を開いたまま、外から 1 本目を消す → 0 番目が「ザネリ」に入れ替わる
    store.save(EntryDraft(
        term="ジョバンニ", category="登場人物", summary="活版所で働く少年。", definition="主人公。",
        relations=[{"to": "ザネリ", "label": "同級生"}],
    ), ref="登場人物/ジョバンニ")

    page.fill("#edgeDialog [data-ref=label]", "級友")
    page.click("#edgeDialog [data-ref=save]")

    status = page.locator("#edgeDialog [data-ref=status].error")
    status.wait_for(timeout=10000)
    assert "図が古くなっています" in status.inner_text()
    # ザネリ側は書き換わっていない
    assert [(r.to, r.label) for r in store.get("登場人物/ジョバンニ").relations] == [("ザネリ", "同級生")]


def test_the_doctor_is_quiet_then_reports_a_broken_reference(page, server, seeded):
    page.goto(f"{server}/doctor")
    page.locator("[data-ref=report] .empty").wait_for(timeout=15000)
    assert "直すところはありません" in page.locator("[data-ref=report]").inner_text()

    entry = store.find_by_surface("ジョバンニ")[0]
    store.save(
        EntryDraft(
            term=entry.term, category=entry.category,
            summary=entry.summary, definition=entry.definition,
            relations=[{"to": "いない人", "label": "兄"}],
        ),
        ref=entry.ref,
    )
    page.click("[data-ref=reload]")
    page.locator(".issue-badge.error").wait_for(timeout=10000)
    text = page.locator("[data-ref=report]").inner_text()
    assert "解決できない関係" in text
    # 壊れた参照は「次に書くべきエントリ」でもある
    assert "いない人 を登録" in text


def test_the_doctor_can_fix_an_entry_in_place(page, server, isolated_dirs):
    """点検からページを渡り歩かずに直せること（直したら消えるところまで）。"""
    store.save(EntryDraft(term="冪等", category="プログラミング", definition="本文。"))
    page.goto(f"{server}/doctor")
    page.locator(".issue-badge").wait_for(timeout=15000)
    assert "要約が無い" in page.locator("[data-ref=report]").inner_text()

    page.click("button:has-text('直す')")
    page.locator("dialog.sheet[open]").wait_for(timeout=10000)
    page.fill("dialog.sheet[open] [data-ref='summary']", "何度実行しても結果が同じであること。")
    page.click("dialog.sheet[open] [data-ref='save']")

    page.locator("[data-ref=report] .empty").wait_for(timeout=15000)
    assert "直すところはありません" in page.locator("[data-ref=report]").inner_text()


def test_content_search_opens_the_file_at_the_hit(page, server, isolated_dirs):
    """横断検索 → ヒットを押す → その文書が開いて、その場所が光る。

    検索語は辞書に無いことのほうが多いので、初出ジャンプ (`[data-gloss]`) では
    寄せられない。見えているテキストで探して光らせるところまで見る。
    """
    base = config.content_dir()
    (base / "一巻.txt").write_text("最初の段落。\n\nカムパネルラは黙っていた。\n", encoding="utf-8")
    (base / "二巻.txt").write_text("何も出てこない話。\n", encoding="utf-8")

    page.goto(f"{server}/")
    page.locator("#files button").first.wait_for(timeout=15000)

    open_content_search(page)
    page.fill("#contentQ", "カムパネルラ")
    page.click("#searchGo")
    page.locator("#searchResults .filelist button").first.wait_for(timeout=15000)
    assert "1 件" in page.locator("#searchResults").inner_text()
    assert "L.3" in page.locator("#searchResults").inner_text()

    page.click("#searchResults .filelist button")
    page.locator("#doc .gloss-flash").wait_for(timeout=15000)
    assert "カムパネルラ" in page.locator("#doc .gloss-flash").inner_text()


def test_content_search_says_when_nothing_matched(page, server, isolated_dirs):
    (config.content_dir() / "a.txt").write_text("何も出てこない話。\n", encoding="utf-8")
    page.goto(f"{server}/")
    page.locator("#files button").first.wait_for(timeout=15000)

    open_content_search(page)
    page.fill("#contentQ", "存在しない語")
    page.click("#searchGo")
    page.locator("#searchResults .empty").wait_for(timeout=15000)
    assert "見つかりませんでした" in page.locator("#searchResults").inner_text()
    assert "1 文書を読みました" in page.locator("#searchStatus").inner_text()


def test_an_epub_gets_a_table_of_contents_that_jumps(page, server, isolated_dirs):
    """epub の章を目次から辿れること。

    位置は作り直さない（章の見出しはすでに本文に入っている）。描き終わった本文を
    頭からなぞって段落に対応づけるので、**対応づけに失敗すると黙って空の目次**に
    なる。ここでしか捕まらない。
    """
    from tests.test_documents import make_epub

    make_epub(
        config.content_dir() / "本.epub",
        [("一、午后の授業", "ジョバンニは考えた。"), ("二、活版所", "活字を拾った。")],
    )

    page.goto(f"{server}/?open=%E6%9C%AC.epub")
    page.locator("#toc button").first.wait_for(timeout=15000)
    assert [b.strip() for b in page.locator("#toc button").all_text_contents()] == [
        "一、午后の授業", "二、活版所",
    ]
    assert page.locator("#tocNote").is_hidden()      # 取りこぼしなし

    page.click("#toc button >> nth=1")
    page.locator("#doc .gloss-flash").wait_for(timeout=10000)
    assert "二、活版所" in page.locator("#doc .gloss-flash").inner_text()


def test_plain_text_has_no_table_of_contents(page, server, isolated_dirs):
    """区切りが 1 つしかない文書に空の目次を出さない。"""
    (config.content_dir() / "a.txt").write_text("ただの文章。\n", encoding="utf-8")
    page.goto(f"{server}/?open=a.txt")
    page.locator("#doc p").first.wait_for(timeout=15000)
    assert page.locator("#toc").is_hidden()
    assert page.locator("#tocHead").is_hidden()


def test_the_glossary_filters_by_tag(page, server, isolated_dirs):
    """タグの絞り込みと、用語ページの `#タグ` からの遷移。

    タグにマスターは無いので、選択肢は `/api/tags` の数え上げから作る。
    """
    store.save(EntryDraft(term="冪等", category="プログラミング",
                          summary="要約。", definition="本文。", tags=["設計原則"]))
    store.save(EntryDraft(term="副作用", category="プログラミング",
                          summary="設計原則の話で出る。", definition="本文。"))

    open_glossary(page, server)
    assert page.locator(".card").count() == 2

    page.select_option("#tagFilter", "設計原則")
    page.wait_for_function("document.querySelectorAll('.card').length === 1", timeout=10000)
    assert "冪等" in page.locator("#list").inner_text()

    # 用語ページの #タグ からも同じ絞り込みに入れること
    page.goto(f"{server}/glossary/プログラミング/冪等")
    page.locator(".chips a").first.wait_for(timeout=SETTLE_MS)
    page.click(".chips a")
    wait_for_glossary(page)
    assert page.locator(".card").count() == 1
    assert page.eval_on_selector("#tagFilter", "n => n.value") == "設計原則"


def test_a_word_can_be_selected_and_registered_from_the_list(page, server, isolated_dirs):
    """一覧の要約に出てきた語を、その場で選んで登録できること。

    カードは `<a>` 全体なので、**そのままだとドラッグがリンク掴みになって
    選択が安定しない**（一覧にだけこの口が無かった理由）。`draggable="false"` で
    選べるようにし、選んだままのクリックでは遷移させないようにしてある。
    """
    store.save(EntryDraft(term="冪等", category="プログラミング",
                          summary="結果整合性と並べて語られる。", definition="本文。"))

    open_glossary(page, server)
    summary = page.locator(".card .s").first
    box = summary.bounding_box()

    # 「結果整合性」をドラッグで選ぶ（ダブルクリックだと語境界の判定に頼ることになる）
    page.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=10)
    page.mouse.up()

    # **選べていること**（リンクを掴んでいたら選択は空のまま）
    page.wait_for_function("!window.getSelection().isCollapsed", timeout=SETTLE_MS)
    # 選んだままのクリックで用語ページへ飛んでいない
    assert page.url.endswith("/glossary")

    page.locator("button.sel-add").wait_for(timeout=SETTLE_MS)
    page.click("button.sel-add")
    dialog = page.locator("dialog.sheet[open]")
    dialog.wait_for(timeout=SETTLE_MS)
    assert page.input_value("dialog.sheet[open] [data-ref=term]").strip() != ""


def test_a_card_still_opens_the_entry(page, server, isolated_dirs):
    """カードを `<a>` から `<div>` にしたので、**飛べることを見張る**。

    見出しは本物のリンクのまま（中クリック・Ctrl クリックが効く）、それ以外の
    ところはクリックで飛ばしている。どちらも壊れると一覧から先に進めなくなる。
    """
    store.save(EntryDraft(term="冪等", category="プログラミング",
                          summary="何度でも同じ。", definition="本文。"))

    open_glossary(page, server)
    # 見出しは <a>。href がそのまま入っていること
    href = page.eval_on_selector(".card .t", "n => n.getAttribute('href')")
    assert href and href.endswith("/glossary/%E3%83%97%E3%83%AD%E3%82%B0%E3%83%A9"
                                  "%E3%83%9F%E3%83%B3%E3%82%B0/%E5%86%AA%E7%AD%89")

    # 要約のところを押しても飛ぶ
    page.click(".card .s")
    page.locator("h1").first.wait_for(timeout=SETTLE_MS)
    assert "冪等" in page.locator("h1").first.inner_text()


def test_the_category_manager_separates_the_two_dictionaries(page, server, isolated_dirs, tmp_path):
    """カテゴリ管理で、フォルダの辞書と全体の辞書を取り違えないこと。

    スコープを渡していなかったため、**ローカルのカテゴリを消そうとして同名の
    グローバル側が消えた**。画面でも見分けが付かなかった。
    """
    from glosspop import categories

    folder = tmp_path / "小説"
    folder.mkdir()
    config.set_content_dir(folder)
    categories.ensure("登場人物")                      # 全体には空で作っておく
    store.save(EntryDraft(term="ザネリ", category="登場人物", scope="local", definition="本文。"))

    # **一覧が描き終わるまで開かない。** カテゴリ管理は読み込み済みの `tree` を
    # そのまま描くだけで、あとから描き直さない。読み込みの前に開くと
    # 「カテゴリがまだありません」のまま固まり、15 秒待って落ちる
    open_glossary(page, server)
    page.click("#manageCats")
    page.locator("dialog.sheet[open] .cat-row").first.wait_for(timeout=SETTLE_MS)
    rows = page.locator("dialog.sheet[open] .cat-row-name").all_text_contents()
    # 同じ名前が 2 つ並ぶので、印が無いと区別が付かない
    assert rows == ["登場人物", "📁 登場人物"]

    # フォルダ側を改名する。全体側は巻き添えにしない
    page.click("dialog.sheet[open] .cat-row >> nth=1 >> button:has-text('名前を変更')")
    page.fill("dialog.sheet[open] input[aria-label='新しいカテゴリ名']", "人物")
    page.click("dialog.sheet[open] button:has-text('保存')")

    page.wait_for_function(
        "[...document.querySelectorAll('dialog.sheet[open] .cat-row-name')]"
        ".some(n => n.textContent === '📁 人物')",
        timeout=15000,
    )
    assert [e.ref for e in store.load_all()] == [".local/人物/ザネリ"]
    # マスターは辞書ごと。全体側は巻き添えにしない
    assert [c.name for c in categories.load()] == ["登場人物"]
    assert [c.name for c in categories.load("local")] == ["人物"]


def test_the_category_manager_catches_up_with_a_slow_load(page, server, isolated_dirs, monkeypatch):
    """**読み込みの前に開かれても、届いた時点で描き直すこと。**

    カテゴリ管理は読み込み済みの一覧をそのまま描くだけなので、間に合わない
    うちに開かれると「カテゴリがまだありません」のまま固まっていた。手元では
    一瞬で返るので絶対に踏まず、**負荷の高い CI でだけ落ちる**（要素が 15 秒
    出てこない、という形。リリースを止めたのがこの類）。

    **待ち時間で競走させない。** 応答を止めておき、確かめてから手で通す
    （`sleep` で遅らせると、今度はこのテストが「たまに落ちる」側に回る。
    playwright の sync API では route の中で寝ると `goto` ごと止まって、
    そもそも再現もしない）。止めるのはサーバ側なので、一覧の描画
    （`/api/entries`）は普通に進む。
    """
    store.save(EntryDraft(term="冪等", category="プログラミング", definition="本文。"))

    gate = threading.Event()
    original = store.category_tree
    monkeypatch.setattr(store, "category_tree", lambda: (gate.wait(30), original())[1])

    try:
        page.goto(f"{server}/glossary")
        page.click("#manageCats")                 # わざと読み込みを待たずに開く
        page.locator("dialog.sheet[open]").wait_for(timeout=SETTLE_MS)
        assert "カテゴリがまだありません" in page.locator("dialog.sheet[open] .body").inner_text()
        gate.set()                                # ここで初めて応答を通す
        page.locator("dialog.sheet[open] .cat-row").first.wait_for(timeout=SETTLE_MS)
        rows = page.locator("dialog.sheet[open] .cat-row-name").all_text_contents()
        assert rows == ["プログラミング"]
    finally:
        gate.set()                                # 落ちてもサーバのスレッドを残さない


def test_the_category_order_can_be_changed(page, server, isolated_dirs, tmp_path):
    """カテゴリの並びを ↑ ↓ で変えられること（フォルダの辞書でも）。

    小説なら 主要人物 → 脇役 の順に読みたい。並び順はマスターが持つので、
    **フォルダ側にマスターが無いとこれができない**（登録順に固定される）。
    """
    from glosspop import categories

    folder = tmp_path / "小説"
    folder.mkdir()
    config.set_content_dir(folder)
    store.save(EntryDraft(term="ザネリ", category="級友", scope="local", definition="本文。"))
    store.save(EntryDraft(term="ジョバンニ", category="主要人物", scope="local", definition="本文。"))
    assert [c.name for c in categories.load("local")] == ["級友", "主要人物"]

    open_glossary(page, server)
    page.click("#manageCats")
    page.locator("dialog.sheet[open] .cat-row").first.wait_for(timeout=SETTLE_MS)
    page.click("dialog.sheet[open] button[aria-label='主要人物 を 1 つ上へ']")

    page.wait_for_function(
        "[...document.querySelectorAll('dialog.sheet[open] .cat-row-name')]"
        ".map(n => n.textContent).join('|') === '📁 主要人物|📁 級友'",
        timeout=15000,
    )
    assert [c.name for c in categories.load("local")] == ["主要人物", "級友"]
    # 一覧の見出しも同じ順になる（用語の並びがマスターに従う）
    page.locator("#list .cat-group h2").first.wait_for(timeout=15000)
    headings = page.locator("#list .cat-group h2 span:first-child").all_text_contents()
    assert headings == ["📁 主要人物", "📁 級友"]


def test_the_reading_position_survives_reopening(page, server, isolated_dirs):
    """長い本を開き直したとき、前回の続きから出ること。

    位置は px ではなく段落の番号で持っている。JS が落ちていると「戻ったつもりで
    先頭のまま」になるが、HTML は正しいままなので単体テストでは捕まらない。
    """
    long_doc = config.content_dir() / "長い本.txt"
    long_doc.write_text("\n\n".join(f"第 {i} 段落。" for i in range(200)), encoding="utf-8")

    page.goto(f"{server}/?open=%E9%95%B7%E3%81%84%E6%9C%AC.txt")
    page.locator("#doc p").first.wait_for(timeout=15000)
    page.wait_for_function("document.querySelectorAll('#doc > *').length > 100", timeout=10000)

    # 途中まで読む → スクロールが止まってから書かれる
    page.evaluate("document.querySelector('main.doc-wrap').scrollTop = 4000")
    page.wait_for_function(
        "JSON.parse(localStorage.getItem('glosspop.reading') || '{}')"
        "[Object.keys(JSON.parse(localStorage.getItem('glosspop.reading') || '{}'))[0]]?.block > 2",
        timeout=10000,
    )

    page.goto(f"{server}/?open=%E9%95%B7%E3%81%84%E6%9C%AC.txt")
    page.locator("#docStatus:not([hidden])").wait_for(timeout=15000)
    assert "前回の続き" in page.locator("#docStatus").inner_text()
    assert page.evaluate("document.querySelector('main.doc-wrap').scrollTop") > 100

    # 「先頭から読む」で戻り、覚えていた位置も捨てる
    page.click("#docStatus button")
    page.wait_for_function(
        "document.querySelector('main.doc-wrap').scrollTop === 0", timeout=10000
    )
    page.goto(f"{server}/?open=%E9%95%B7%E3%81%84%E6%9C%AC.txt")
    page.locator("#doc p").first.wait_for(timeout=15000)
    assert page.locator("#docStatus").is_hidden()


def test_the_reading_position_is_not_restored_when_jumping_to_a_first_use(page, server, seeded):
    """初出へ飛ぶと決まっているときは読書位置を戻さない（案内だけが嘘になる）。"""
    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md&term=%E3%82%AB%E3%83%A0%E3%83%91%E3%83%8D%E3%83%AB%E3%83%A9")
    page.locator("a.gloss-link").first.wait_for(timeout=15000)
    assert "前回の続き" not in page.locator("#docStatus").inner_text()


def test_the_graph_filters_by_a_category_whose_name_has_a_space(page, server, isolated_dirs):
    """カテゴリ名に空白を使えるので、選択の値は空白で割れない。"""
    store.save(EntryDraft(term="ジョバンニ", category="銀河 鉄道", summary="主人公。", definition="本文。"))
    store.save(EntryDraft(term="冪等", category="プログラミング", summary="要約。", definition="本文。"))

    page.goto(f"{server}/graph")
    page.locator("svg.rel-graph").wait_for(timeout=15000)
    assert page.locator("svg.rel-graph .rel-node").count() == 2

    page.select_option("#category", label="銀河 鉄道")
    page.wait_for_function(
        "document.querySelectorAll('svg.rel-graph .rel-node').length === 1", timeout=10000
    )
    assert "ジョバンニ" in (page.locator("svg.rel-graph").text_content() or "")


def test_the_settings_dialog_exports_and_imports_the_glossary(page, server, isolated_dirs, tmp_path):
    """書き出し → 置き換え → 控えが残る、まで通す。

    **このアプリで唯一データが消える画面**なので、確認を出すところと控えの場所を
    出すところまで見る。
    """
    from glosspop import archive

    store.save(EntryDraft(term="冪等", category="プログラミング", definition="本文。"))
    exported = tmp_path / "backup.zip"
    exported.write_bytes(archive.export_bytes())

    # 書き出したあとに増やした語は、取り込みで消える側
    store.save(EntryDraft(term="結果整合性", category="プログラミング", definition="本文。"))

    page.goto(f"{server}/glossary")
    page.locator("#settings").wait_for(timeout=15000)
    page.click("#settings")
    page.locator("dialog.sheet[open] [data-ref=importPick]").wait_for(timeout=10000)

    page.on("dialog", lambda d: d.accept())      # confirm() を通す
    page.set_input_files("dialog.sheet[open] [data-ref=importFile]", str(exported))

    result = page.locator("dialog.sheet[open] [data-ref=result]")
    result.wait_for(timeout=15000)
    assert "1 語 / 1 カテゴリに置き換えました" in result.inner_text()
    assert "控えは" in result.inner_text()

    assert {e.term for e in store.load_all()} == {"冪等"}
    backups = list((config.DATA_ROOT / "data" / archive.BACKUP_DIR_NAME).glob("backup-*.zip"))
    assert len(backups) == 1
    # 再起動を促していない（保存先は変わらないので読み直しだけで足りる）
    assert "開き直して" not in result.inner_text()


def test_the_settings_dialog_can_export_one_category(page, server, isolated_dirs):
    """辞書の一部だけを渡せる。**決めるのは書き出す側だけ。**

    選んだ時点で「何語入るか」と「渡した先で行き先の無くなる関係が何本か」を
    出すところまで見る（押したあとでは気付けない）。
    """
    store.save(EntryDraft(term="ソース", category="料理", definition="本文。"))
    store.save(EntryDraft(
        term="冪等", category="プログラミング", definition="本文。",
        relations=[{"to": "料理/ソース", "label": "例"}],
    ))

    page.goto(f"{server}/glossary")
    page.locator("#settings").wait_for(timeout=15000)
    page.click("#settings")
    scope = page.locator("dialog.sheet[open] [data-ref=exportScope]")
    scope.wait_for(timeout=10000)

    note = page.locator("dialog.sheet[open] [data-ref=exportNote]")
    page.wait_for_function(
        "() => (document.querySelector('dialog.sheet[open] [data-ref=exportNote]')"
        "?.textContent || '').includes('辞書全体')",
        timeout=10000,
    )
    assert "2 語" in note.inner_text()

    # カテゴリは読み込みが届いてから足される（開いた時点では「辞書全体」だけ）
    page.wait_for_function(
        "() => document.querySelectorAll("
        "'dialog.sheet[open] [data-ref=exportScope] option').length > 1",
        timeout=10000,
    )
    scope.select_option("プログラミング")
    page.wait_for_function(
        "() => (document.querySelector('dialog.sheet[open] [data-ref=exportNote]')"
        "?.textContent || '').includes('このカテゴリ')",
        timeout=10000,
    )
    said = note.inner_text()
    assert "1 語" in said
    # **黙って切らない。** 渡した先で相手を失う関係は数で出す
    assert "行き先の無くなる関係が 1 本" in said and "冪等 → ソース" in said


def test_the_settings_dialog_restores_one_entry_from_a_backup(page, server, isolated_dirs):
    """控えの中を見て、**1 件だけ**戻せる。

    併合の衝突は「取り込む側が勝つ」なので、**上書きされた語は控えにしか残らない**。
    zip を手で開かせるのでは、その約束が半分しか果たせていない。
    """
    from glosspop import archive

    store.save(EntryDraft(term="冪等", category="プログラミング", definition="本文。"))
    store.save(EntryDraft(term="ソース", category="料理", definition="本文。"))
    archive.write_backup()
    store.delete(store.find_by_surface("ソース")[0].ref)
    assert {e.term for e in store.load_all()} == {"冪等"}

    page.goto(f"{server}/glossary")
    page.locator("#settings").wait_for(timeout=15000)
    page.click("#settings")
    # 件数は畳んだままでも見える（開かないと分からない、では溜まりに気付けない）
    count = page.locator("dialog.sheet[open] [data-ref=backupCount]")
    count.wait_for(timeout=10000)
    page.wait_for_function(
        "() => (document.querySelector('dialog.sheet[open] [data-ref=backupCount]')"
        "?.textContent || '').includes('1 件')",
        timeout=10000,
    )
    page.click("dialog.sheet[open] [data-ref=backupBox] summary")
    page.locator("dialog.sheet[open] [data-ref=backupList] .backup-row").first.wait_for(
        timeout=10000
    )

    # 控えを開くと中身が出る（戻すと上書きになるかどうかも出る）
    page.locator("dialog.sheet[open] .backup-head button").first.click()
    row = page.locator("dialog.sheet[open] .backup-entry", has_text="料理/ソース")
    row.wait_for(timeout=10000)
    assert row.locator("button").inner_text() == "戻す"        # 手元に無いので上書きではない

    row.locator("button").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('dialog.sheet[open] .backup-entry')]"
        ".some((n) => n.textContent.includes('戻しました'))",
        timeout=10000,
    )
    assert {e.term for e in store.load_all()} == {"冪等", "ソース"}


def test_the_settings_dialog_can_merge_instead_of_replacing(page, server, isolated_dirs, tmp_path):
    """併合を選ぶと、**手元にしか無い語が消えないこと**。

    置き換えと同じ画面・同じボタンなので、選び間違えると辞書が丸ごと消える。
    実行の前に「足す / 上書き / 消える」の件数を出しているところまで見る。
    """
    from glosspop import archive

    store.save(EntryDraft(term="冪等", category="プログラミング", definition="手元の説明。"))
    exported = tmp_path / "backup.zip"
    exported.write_bytes(archive.export_bytes())

    # 書き出したあとに増やした語。**置き換えなら消えるが、併合では残る**
    store.save(EntryDraft(term="結果整合性", category="プログラミング", definition="本文。"))

    page.goto(f"{server}/glossary")
    page.locator("#settings").wait_for(timeout=SETTLE_MS)
    page.click("#settings")
    page.locator("dialog.sheet[open] [data-ref=importPick]").wait_for(timeout=SETTLE_MS)
    page.select_option("dialog.sheet[open] [data-ref=importMode]", "merge")

    # 押す前に何が起きるかを出していること（黙って実行させない）
    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.accept()))
    page.set_input_files("dialog.sheet[open] [data-ref=importFile]", str(exported))

    result = page.locator("dialog.sheet[open] [data-ref=result]")
    result.wait_for(timeout=SETTLE_MS)
    assert {e.term for e in store.load_all()} == {"冪等", "結果整合性"}
    assert "併合します" in messages[0]
    assert "変わらない 1 語" in messages[0]
    assert "消える" not in messages[0]          # 併合では消えない
    assert "控えは" in result.inner_text()


def test_importing_a_foreign_zip_says_so_and_changes_nothing(page, server, isolated_dirs, tmp_path):
    store.save(EntryDraft(term="冪等", category="プログラミング", definition="本文。"))
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")

    page.goto(f"{server}/glossary")
    page.locator("#settings").wait_for(timeout=15000)
    page.click("#settings")
    page.locator("dialog.sheet[open] [data-ref=importPick]").wait_for(timeout=10000)

    page.on("dialog", lambda d: d.accept())
    page.set_input_files("dialog.sheet[open] [data-ref=importFile]", str(junk))

    status = page.locator("dialog.sheet[open] [data-ref=status].error")
    status.wait_for(timeout=15000)
    assert "zip として読めません" in status.inner_text()
    assert {e.term for e in store.load_all()} == {"冪等"}


def test_the_settings_dialog_moves_the_data_root(page, server, isolated_dirs, tmp_path, monkeypatch):
    """更新のたびに手でコピーしなくて済むように、保存先を画面から変えられること。"""
    from glosspop import config as cfg

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", settings_file)
    # 実際の配置 (DATA_ROOT/data/glossary) に合わせる。合わせないと複製に乗らない
    monkeypatch.setattr(cfg, "GLOSSARY_DIR", tmp_path / "data" / "glossary")
    monkeypatch.setattr(cfg, "CATEGORIES_FILE", tmp_path / "data" / "categories.yaml")
    monkeypatch.delenv("GLOSSPOP_DATA_ROOT", raising=False)
    cfg.ensure_dirs()
    store.invalidate()
    store.save(EntryDraft(term="冪等", category="プログラミング", summary="要約。", definition="本文。"))

    page.goto(f"{server}/glossary")
    page.click("#settings")
    page.locator("dialog.sheet[open]").wait_for(timeout=15000)
    # **ダイアログが開いた時点ではまだ空。** 中身は /api/settings を待って描かれるので、
    # 開いた直後に読むと、負荷の高いときだけ落ちる（実際に全体実行で 1 度落ちた）。
    # 保存先の選択も paintMode が後から上書きするので、描き終わってから触る
    page.locator("dialog.sheet[open] [data-ref='paths'] dd").first.wait_for(timeout=15000)
    # いま何がどこにあるかが見えること（コピーの取りこぼしを防ぐのが目的）
    body = page.locator("dialog.sheet[open]").inner_text()
    assert "専用ウィンドウの設定・お気に入り" in body
    assert str(settings_file) in body

    # いまの保存先の外を選ぶ（中を選ぶと入れ子になるのでサーバが弾く）
    target = tmp_path.parent / f"{tmp_path.name}-移動先"
    page.check("dialog.sheet[open] [data-ref='modeCustom']")
    page.fill("dialog.sheet[open] [data-ref='path']", str(target))
    page.click("dialog.sheet[open] [data-ref='save']")

    page.locator("dialog.sheet[open] [data-ref='result']").wait_for(timeout=15000)
    note = page.locator("dialog.sheet[open] [data-ref='result']").inner_text()
    # 走っているプロセスは古い場所を見たまま。黙ると「移したのに反映されない」になる
    assert "次の起動から" in note and "開き直して" in note
    assert (target / "data" / "glossary" / "プログラミング" / "冪等.md").exists()
    # 元は消さない（戻れるようにする）
    assert (cfg.GLOSSARY_DIR / "プログラミング" / "冪等.md").exists()


def _wait_for_status(page, ref, text, timeout=15000, attr="data-ref"):
    status = page.locator(f"dialog.sheet[open] [{attr}={ref}]")
    for _ in range(timeout // 100):
        if text in status.inner_text():
            return status.inner_text()
        page.wait_for_timeout(100)
    raise AssertionError(f"{ref} に「{text}」が出ない: {status.inner_text()!r}")


#: 文体と顔の部品（`ai-style.js`）の中身。**設定ダイアログとビューアのサイドバーで
#: 同じもの**なので、印は `data-sref` にしてある（外から `[data-ref]` を集める
#: 呼ぶ側と衝突させない）
STYLE = "dialog.sheet[open] [data-sref=%s]"


def test_the_settings_dialog_sets_the_writing_style(page, server, isolated_dirs):
    """文体の指定は AI タブにある。**例を押せばそのまま入り、保存すれば次から効く。**

    AI を呼ぶ経路そのものは手で確かめるしかないが、「押しても入らない」
    「保存したのに残らない」は HTML が正しくても起きるのでここで見張る。
    """
    from glosspop import ai

    page.goto(f"{server}/glossary")
    page.locator("#settings").wait_for(timeout=15000)
    page.click("#settings")
    page.click("dialog.sheet[open] [data-tab='ai']")

    presets = page.locator(STYLE % "presets" + " .chip")
    presets.first.wait_for(timeout=15000)
    presets.first.click()
    typed = page.input_value(STYLE % "style")
    assert typed                            # 例を押したら入力欄に入ること

    page.click(STYLE % "save")
    _wait_for_status(page, "status", "保存しました", attr="data-sref")
    # **次の下書きからそのまま効く**（再起動を挟まない）
    assert ai.style() == typed
    assert typed in ai.build_prompt("冪等")


def test_the_settings_dialog_keeps_a_style_per_folder(page, server, isolated_dirs):
    """📁 とグローバルは**別々に持てて、どちらが効いているかが画面に出る。**

    片方だけ見せると「全体に書いたのに効かない」になる（優先順は黙らない）。
    """
    from glosspop import ai, config

    page.goto(f"{server}/glossary")
    page.locator("#settings").wait_for(timeout=15000)
    page.click("#settings")
    page.click("dialog.sheet[open] [data-tab='ai']")
    page.locator(STYLE % "presets" + " .chip").first.wait_for(timeout=15000)

    page.fill(STYLE % "style", "全体の口調")
    page.click(STYLE % "save")
    _wait_for_status(page, "status", "保存しました", attr="data-sref")

    # 📁 に切り替えると入力欄も入れ替わる（全体のぶんを引きずらない）
    page.select_option(STYLE % "scope", "local")
    assert page.input_value(STYLE % "style") == ""
    page.fill(STYLE % "style", "この作品の口調")
    page.click(STYLE % "save")
    _wait_for_status(page, "status", "保存しました", attr="data-sref")

    assert (config.content_dir() / ".glosspop" / "style.md").is_file()
    assert ai.style() == "この作品の口調"
    note = page.locator(STYLE % "note").inner_text()
    assert "📁 このフォルダ" in note and "全体にも指定があります" in note
    # どこに置かれたかも出す（祖先のフォルダに書かれることがある）
    assert ".glosspop" in page.locator(STYLE % "where").inner_text()


def test_the_settings_entry_is_labelled_and_next_to_the_nav(page, server, isolated_dirs):
    """歯車だけを右端に置くと気づかれない。文字つきでナビの直後に出すこと。"""
    page.goto(f"{server}/glossary")
    button = page.locator(".topbar #settings")
    button.wait_for(timeout=15000)
    assert "設定" in button.inner_text()

    nav = page.locator(".topbar .topnav").bounding_box()
    box = button.bounding_box()
    meta = page.locator(".topbar .meta").bounding_box()
    # ナビの直後（＝タイトル寄り）にあること。右端の隅ではない
    assert nav["x"] < box["x"] < meta["x"]
    assert box["x"] - (nav["x"] + nav["width"]) < 40


#: テーマで切り替わる CSS 変数。2 つのダークのブロックが一致するかを見るのに使う
THEME_VARS = [
    "--bg", "--panel", "--panel-2", "--border", "--border-strong",
    "--fg", "--fg-dim", "--fg-faint", "--accent", "--accent-soft",
    "--accent-fg", "--danger", "--warn", "--shadow",
]

_READ_VARS = """() => {
  const s = getComputedStyle(document.documentElement);
  return Object.fromEntries(%s.map((n) => [n, s.getPropertyValue(n).trim()]));
}"""


def read_theme_vars(page):
    return page.evaluate(_READ_VARS % THEME_VARS)


def font_px(page, selector):
    """描かれている文字の大きさ（px）。計算後の値なので calc も比も解決済み。"""
    return page.evaluate(
        "(sel) => parseFloat(getComputedStyle(document.querySelector(sel)).fontSize)",
        selector,
    )


def test_the_font_size_setting_grows_everything_and_survives_a_reload(page, server, isolated_dirs):
    """文字の大きさ。**選んだ瞬間に効き、開き直しても残る。**

    大きさの正は style.css の ``--fs-base`` 1 つで、ほかの字はそこから比で作って
    ある。**周りの px を直して回る形に戻すと、直し漏れた 1 か所だけが小さいまま
    残る** —— そこは画面を開くまで分からない。だから本文だけでなく、比で付いて
    くる側（topbar の小さい字）も一緒に大きくなることを見る。

    開き直しの側を担当しているのは head のインライン script で、**5 つの HTML に
    同じ写しがある**。1 つ直し忘れると、そのページだけ設定が効かない。
    """
    page.goto(f"{server}/glossary")
    wait_for_glossary(page)
    small_body = font_px(page, "body")
    small_meta = font_px(page, ".topbar .meta")

    page.click("#settings")
    page.locator("dialog.sheet[open] [data-ref='fontSize']").wait_for(timeout=SETTLE_MS)
    page.select_option("dialog.sheet[open] [data-ref='fontSize']", "xlarge")
    page.wait_for_function("document.documentElement.dataset.fontsize === 'xlarge'", timeout=5000)

    assert font_px(page, "body") > small_body
    assert font_px(page, ".topbar .meta") > small_meta, "基準に付いてこない字が残っている"

    # 描画の前に当たること（あとから当てると一度小さい字で描いてから飛ぶ）
    page.goto(f"{server}/")
    assert page.evaluate("document.documentElement.dataset.fontsize") == "xlarge"
    assert font_px(page, "body") > small_body

    # 既定に戻すと属性ごと外れる（テーマの system と同じ扱い）
    page.click("#settings")
    page.locator("dialog.sheet[open] [data-ref='fontSize']").wait_for(timeout=SETTLE_MS)
    page.select_option("dialog.sheet[open] [data-ref='fontSize']", "medium")
    page.wait_for_function("!document.documentElement.dataset.fontsize", timeout=5000)
    assert font_px(page, "body") == small_body


def test_the_theme_can_be_switched_and_survives_a_reload(page, server, isolated_dirs):
    page.goto(f"{server}/glossary")
    page.click("#settings")
    page.locator("dialog.sheet[open] [data-ref='theme']").wait_for(timeout=15000)
    light = read_theme_vars(page)

    page.select_option("dialog.sheet[open] [data-ref='theme']", "dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'", timeout=5000)
    dark = read_theme_vars(page)
    assert dark["--bg"] != light["--bg"], "ダークにしても色が変わっていない"

    # 描画前に当たること（あとから当てると一瞬白く光る）
    page.goto(f"{server}/")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert read_theme_vars(page)["--bg"] == dark["--bg"]

    page.click("#settings")
    page.locator("dialog.sheet[open] [data-ref='theme']").wait_for(timeout=10000)
    page.select_option("dialog.sheet[open] [data-ref='theme']", "system")
    page.wait_for_function("!document.documentElement.dataset.theme", timeout=5000)


def test_both_dark_definitions_agree(server, seeded):
    """CSS にブロックの再利用が無いのでダークの値を 2 か所に複製している。

    片方だけ直すと「OS がダークのときだけ色が違う」という気づきにくい壊れ方を
    するので、実際に描かせて突き合わせる。
    """
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception as exc:                       # noqa: BLE001
            pytest.skip(f"Chrome を起動できません: {exc}")
        try:
            # OS がダーク + 明示なし → メディアクエリ側
            context = browser.new_context(color_scheme="dark")
            page = context.new_page()
            page.goto(f"{server}/glossary")
            page.locator(".topbar").wait_for(timeout=15000)
            by_media = read_theme_vars(page)
            context.close()

            # OS がライト + 明示的にダーク → :root[data-theme="dark"] 側
            context = browser.new_context(color_scheme="light")
            page = context.new_page()
            page.goto(f"{server}/glossary")
            page.locator(".topbar").wait_for(timeout=15000)
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
            by_attribute = read_theme_vars(page)
            context.close()
        finally:
            browser.close()

    assert by_media == by_attribute


def test_light_wins_over_a_dark_os(server, seeded):
    """OS がダークでも「ライト」を選んだらライトになること。"""
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception as exc:                       # noqa: BLE001
            pytest.skip(f"Chrome を起動できません: {exc}")
        try:
            context = browser.new_context(color_scheme="dark")
            page = context.new_page()
            page.goto(f"{server}/glossary")
            page.locator(".topbar").wait_for(timeout=15000)
            dark = read_theme_vars(page)
            page.evaluate("document.documentElement.dataset.theme = 'light'")
            assert read_theme_vars(page)["--bg"] != dark["--bg"]
            context.close()
        finally:
            browser.close()


def test_extracting_offers_another_name_as_an_alias(page, server, seeded, monkeypatch):
    """抽出が見つけた「別の呼び方」を、新しい語ではなく既存の語の別名として足す。

    同じ人物が呼び方ごとに別エントリへ割れると、本文のリンク先も相関図のノードも
    二重になる。AI を呼ぶ経路なので claude の応答は差し替えて画面だけを見る。
    """
    from glosspop import ai

    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "_generate", lambda prompt, **_: json.dumps([
        {"term": "ジョバンニ君", "alias_of": "ジョバンニ", "why": "同じ人物の呼び方"},
    ]))
    doc = config.content_dir() / "銀河.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\nジョバンニ君と呼ばれた。\n", encoding="utf-8")

    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=15000)
    page.click("#extract")
    page.locator("dialog.sheet .kind-list .check").first.wait_for(timeout=10000)
    page.click("dialog.sheet button:has-text('種別で候補を抽出する')")

    alias_button = page.locator("dialog.sheet button:has-text('別名に追加')")
    alias_button.wait_for(timeout=20000)
    assert "ジョバンニ君" in page.locator("dialog.sheet .cand-list").inner_text()
    alias_button.click()
    page.locator("dialog.sheet .status:has-text('別名を 1 件追加しました')").wait_for(timeout=10000)

    # 新しいエントリは増えず、既存の語に別名が付いていること
    entries = store.load_all()
    assert [e.term for e in entries] == ["カムパネルラ", "ジョバンニ"]
    assert store.find_by_surface("ジョバンニ君")[0].term == "ジョバンニ"


def test_relations_can_be_drafted_then_continued(page, server, seeded, monkeypatch):
    """下書き → 書き込み →「続けて探す」で**書いたぶんが除かれる**まで通す。

    2 回目はサーバが「すでに関係が書かれています」として落とすので、同じ組が
    二重に出ない。閉じて開き直させないためのボタン。

    **入り口はビューア（表示中の文書）。** 相関図には置かない —— あちらは辞書
    全体を出すので、下書きが読む範囲と一致しない（→ docs/open-questions.md の 7 番）。
    """
    from glosspop import ai

    store.save(EntryDraft(term="ザネリ", category="登場人物",
                          summary="級友。", definition="同級生。"))
    monkeypatch.setattr(ai, "available", lambda: True)
    answers = [
        [{"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友", "back": "親友"}],
        [{"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友"},   # 2 回目も同じ組を返す
         {"from": "ジョバンニ", "to": "ザネリ", "label": "同級生"}],
    ]
    monkeypatch.setattr(ai, "_generate", lambda prompt, **_: json.dumps(answers.pop(0)))

    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=15000)
    page.click("#draftRelations")
    page.locator("dialog.sheet [data-ref=spoiler]").wait_for(timeout=10000)
    page.select_option("dialog.sheet [data-ref=spoiler]", "full")
    page.click("dialog.sheet [data-ref=go]")

    page.locator("dialog.sheet .cand-list li").first.wait_for(timeout=20000)
    page.click("dialog.sheet [data-ref=go]")            # チェックした 1 本を書き込む
    page.locator("dialog.sheet [data-ref=more]:visible").wait_for(timeout=15000)
    assert store.get("登場人物/ジョバンニ").relations[0].label == "親友"

    page.click("dialog.sheet [data-ref=more]")
    page.locator("dialog.sheet .cand-list li").first.wait_for(timeout=20000)
    rows = page.locator("dialog.sheet .cand-list li")
    # すでに書いた組は落ち、新しい組だけが残る
    assert rows.count() == 1
    assert "ザネリ" in rows.first.inner_text()
    assert "すでに関係が書かれています" in page.locator("dialog.sheet [data-ref=dropped]").inner_text()


def test_the_settings_dialog_separates_the_ai_tab(page, server, isolated_dirs):
    """設定はタブで分ける。**フッタの「保存」は一般タブのものなので AI では出さない。**

    出しっぱなしにすると、AI を変えたあとにこちらを押して「保存したのに効かない」
    になる（AI 側は自前の保存ボタンを持つ）。
    """
    page.goto(f"{server}/glossary")
    page.locator(".topbar").wait_for(timeout=15000)
    page.click("#settings")
    page.locator("dialog.sheet .sheet-tabs").wait_for(timeout=10000)

    # 開いた直後は「一般」。データの保存先が見えていて、AI は隠れている
    assert page.locator("dialog.sheet [data-panel=general]").is_visible()
    assert not page.locator("dialog.sheet [data-panel=ai]").is_visible()
    assert page.locator("dialog.sheet [data-ref=save]").is_visible()

    page.click("dialog.sheet [data-tab=ai]")
    assert page.locator("dialog.sheet [data-panel=ai]").is_visible()
    assert not page.locator("dialog.sheet [data-panel=general]").is_visible()
    assert not page.locator("dialog.sheet [data-ref=save]").is_visible()
    assert page.get_attribute("dialog.sheet [data-tab=ai]", "aria-selected") == "true"

    # 矢印キーでも戻れる（tablist の作法）
    page.keyboard.press("ArrowLeft")
    assert page.locator("dialog.sheet [data-panel=general]").is_visible()


def test_the_settings_dialog_switches_the_ai(page, server, isolated_dirs, monkeypatch):
    """⚙ でモデルと思考の深さを選べて、**次の呼び出しから効く**こと。"""
    from glosspop import llm

    page.goto(f"{server}/glossary")
    page.locator(".topbar").wait_for(timeout=15000)
    page.click("#settings")
    # AI は別のタブ。開くまでは触れない（タブで分けたのはここが見つけやすいように）
    page.locator("dialog.sheet [data-tab=ai]").click()
    page.locator("dialog.sheet [data-ref=aiProvider]").wait_for(timeout=10000)

    # 既定は Claude。モデルの候補はアプリが持っている（API を叩かない）
    assert page.eval_on_selector("[data-ref=aiProvider]", "n => n.value") == "claude"
    page.wait_for_function(
        "document.querySelectorAll('#gp-ai-models option').length >= 4", timeout=10000
    )

    page.fill("[data-ref=aiModel]", "opus")
    page.select_option("[data-ref=aiEffort]", "high")
    page.click("[data-ref=aiSave]")
    page.locator("[data-ref=aiStatus]:has-text('保存しました')").wait_for(timeout=10000)

    # サーバ側で解決した値が変わっていること（再起動を挟まない）
    current = llm.resolve()
    assert current["model"] == "opus" and current["effort"] == "high"


def test_the_settings_dialog_never_shows_the_gemini_key(page, server, isolated_dirs, monkeypatch):
    """鍵は「登録済み」とだけ出す。**値を返す口を作らない。**"""
    from glosspop import config, llm

    # 提供元を切り替えるとモデル一覧を API から取りに行く（外へ出さない）
    monkeypatch.setattr(llm, "list_gemini_models", lambda *a, **k: [
        {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash", "thinking": True},
    ])
    config.save_settings({**config.load_settings(), "gemini_api_key": "秘密の鍵"})
    page.goto(f"{server}/glossary")
    page.locator(".topbar").wait_for(timeout=15000)
    page.click("#settings")
    # AI は別のタブ。開くまでは触れない（タブで分けたのはここが見つけやすいように）
    page.locator("dialog.sheet [data-tab=ai]").click()
    page.locator("dialog.sheet [data-ref=aiProvider]").wait_for(timeout=10000)
    page.select_option("[data-ref=aiProvider]", "gemini")
    page.locator("[data-ref=aiKeyRow]:visible").wait_for(timeout=10000)

    assert page.eval_on_selector("[data-ref=aiKey]", "n => n.value") == ""
    assert "登録済み" in page.eval_on_selector("[data-ref=aiKey]", "n => n.placeholder")
    assert "秘密の鍵" not in page.content()


def test_the_update_notice_appears(page, server, isolated_dirs):
    """新しい版があると topbar に出る（押し付けず、リンク 1 本だけ）。"""
    page.goto(f"{server}/glossary")
    notice = page.locator(".topbar #update-notice")
    notice.wait_for(timeout=15000)
    assert "v99.0.0" in notice.inner_text()
    assert notice.get_attribute("href").endswith("/releases/latest")


#: 折り返しているボタンの文字を集める。**隠れているものは見ない**（`hidden` の
#: パネルや閉じたダイアログまで拾うと、原因の分からない失敗になる）。
#: 唯一の例外が横断検索の抜粋 —— ラベルではなく本文なので、1 行に切ると
#: 何にヒットしたのか分からなくなる
_WRAPPED_LABELS = """() => {
  const bad = [];
  for (const b of document.querySelectorAll("button, .btn, .chip, .topnav a")) {
    if (!b.getClientRects().length) continue;
    if (b.closest(".search-results")) continue;
    const wrapped = b.scrollHeight > b.clientHeight + 1;
    const nowrap = getComputedStyle(b).whiteSpace === "nowrap";
    if (wrapped || !nowrap) bad.push((b.textContent || "").trim().slice(0, 24));
  }
  return bad;
}"""


def _assert_no_wrapped_labels(page, where: str):
    bad = page.evaluate(_WRAPPED_LABELS)
    assert not bad, f"{where}: ボタンの文字が折り返しています: {bad}"


def test_no_button_label_ever_wraps(page, server, seeded):
    """**ボタンの文字は決して折り返さない。**

    縦に割れたラベル（「保 / 存」）は読めず、押せる範囲も分からなくなる。
    幅は周り（行の折り返し・隣の基準幅・`…`）で吸収する約束なので、
    **狭い画面でこそ確かめる** —— 広い画面では絶対に踏まない。

    HTML も JS も正しいまま崩れるので、画面を描かせないと気付けない。
    """
    for width in (1280, 380):
        page.set_viewport_size({"width": width, "height": 900})

        page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
        page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
        _assert_no_wrapped_labels(page, f"ビューア ({width}px)")

        open_glossary(page, server)
        _assert_no_wrapped_labels(page, f"一覧 ({width}px)")

        # カテゴリ管理は、ボタンが縮まないと名前の側が 1 文字ずつ縦に割れる
        page.click("#manageCats")
        page.locator("dialog.sheet[open] .cat-row").first.wait_for(timeout=SETTLE_MS)
        _assert_no_wrapped_labels(page, f"カテゴリ管理 ({width}px)")
        page.keyboard.press("Escape")

        page.goto(f"{server}/glossary/登場人物/ジョバンニ")
        page.locator(".entry-actions").wait_for(timeout=SETTLE_MS)
        _assert_no_wrapped_labels(page, f"用語ページ ({width}px)")

        page.goto(f"{server}/graph")
        page.locator(".graph-toolbar").wait_for(timeout=SETTLE_MS)
        _assert_no_wrapped_labels(page, f"相関図 ({width}px)")

        page.goto(f"{server}/doctor")
        page.locator(".topbar #settings").wait_for(timeout=SETTLE_MS)
        page.click("#settings")
        page.locator("dialog.sheet[open] [data-ref='theme']").wait_for(timeout=SETTLE_MS)
        _assert_no_wrapped_labels(page, f"設定 ({width}px)")
        page.keyboard.press("Escape")


def test_the_source_tabs_are_exclusive(page, server, seeded):
    """**フォルダと URL はどちらか一方。**

    `config.set_reading_url()` が立っていれば URL 側の辞書、そうでなければ
    フォルダの辞書、という不変条件をそのまま画面にしている。両方が同時に
    見えていると、**下の AI 設定がどちらの辞書の話なのか読めない**。
    """
    page.goto(f"{server}/")
    page.locator("#files button").first.wait_for(timeout=SETTLE_MS)

    # 既定はフォルダ。URL 側は畳まれている
    assert page.locator("#sourcePanelFolder").is_visible()
    assert page.locator("#sourcePanelUrl").is_hidden()

    page.click("#sourceTabUrl")
    assert page.locator("#url").is_visible()
    assert page.locator("#sourcePanelFolder").is_hidden()

    # 選んだタブは覚える（覆いを何度も開き直すので、毎回選び直させない）
    page.goto(f"{server}/")
    page.locator("#url").wait_for(state="visible", timeout=SETTLE_MS)

    # **開いたものに合わせて戻る。** 手で選んだタブのまま置き去りにしない
    page.click("#sourceTabFolder")
    page.locator("#files button").first.click()
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
    assert page.locator("#sourcePanelFolder").is_visible()


def test_the_face_can_be_replaced_from_the_sidebar(page, server, seeded):
    """サイドバーの AI 設定から語り手の顔を差し替えて、吹き出しに出るまで。

    **設定ダイアログと同じ部品**（`ai-style.js`）なので、ここが動いていれば
    両方が動いている。画面から書ける口を作った以上、**中身が画像でないものを
    弾くところ**まで見ないと、`/api/persona` が配る先に何でも置ける。
    """
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c630001000005000101"
        "0d0a2db40000000049454e44ae426082"
    )
    good = config.content_dir() / "顔.png"
    good.write_bytes(png)
    bad = config.content_dir() / "偽物.png"
    bad.write_bytes(b"<html><script>alert(1)</script></html>")

    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
    # サイドバーは 📁 だけを出す（全体の指定は ⚙ にある）
    row = page.locator("#sideAi .persona-row").first
    row.wait_for(timeout=SETTLE_MS)
    assert "📁" in row.inner_text()
    # **狭いのでパスは出さない**（全文は ⚙ 側。折り返した長いパスが枠の大半を食う）
    assert str(config.content_dir()) not in page.locator("#sideAi").inner_text()
    assert page.locator("#sideAi [data-sref=where]").is_hidden()
    # 「何を読むか」と「どう書かせるか」は線で切る（見出しの色は薄い）
    assert page.locator(".side .side-rule").count() == 1

    def choose(path):
        # ボタン → ファイル選択、という本物の経路で通す（隠した input へ直接
        # 入れると、どのスコープに入れるのかを決めている行が素通りする）
        with page.expect_file_chooser() as chooser:
            page.locator("#sideAi .persona-row button").first.click()
        chooser.value.set_files(str(path))

    # 画像でないものは弾かれ、顔は付かない。**結果は顔の側の行に出る**
    # （文体の保存ボタンの隣に出すと、そちらを押した結果に見える）
    face_status = page.locator("#sideAi [data-sref=faceStatus]")
    choose(bad)
    page.locator("#sideAi [data-sref=faceStatus].error").wait_for(timeout=SETTLE_MS)
    assert "画像として読めません" in face_status.inner_text()
    assert page.locator("#sideAi img.persona-face").count() == 0

    choose(good)
    page.locator("#sideAi img.persona-face").wait_for(timeout=SETTLE_MS)
    assert (config.content_dir() / ".glosspop" / "persona.png").is_file()


def test_the_first_only_option_moved_to_the_settings_and_still_works(page, server, seeded):
    """表示オプションは ⚙ にある。**押した瞬間に本文へ効く。**

    設定ダイアログはビューアの上に開くので、保存や再読み込みを挟まずに
    描き直せること（テーマと同じ扱い）まで見る。
    """
    doc = config.content_dir() / "繰り返し.md"
    doc.write_text("ジョバンニ。\n\nジョバンニ。\n", encoding="utf-8")

    page.goto(f"{server}/?open={quote('繰り返し.md')}")
    page.locator("a.gloss-link").first.wait_for(timeout=SETTLE_MS)
    assert page.locator("#doc a.gloss-link").count() == 2

    page.click("#settings")
    box = page.locator("dialog.sheet[open] [data-ref=firstOnly]")
    box.wait_for(timeout=SETTLE_MS)
    box.check()
    page.keyboard.press("Escape")

    page.wait_for_function(
        "() => document.querySelectorAll('#doc a.gloss-link').length === 1",
        timeout=SETTLE_MS,
    )


#: 用語ごとの画像に使う 1x1 の PNG（本物のバイト列。`imagefmt` が中身で見分ける）
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)


def test_a_term_can_have_its_own_image(page, server, seeded):
    """用語ページから画像を入れると、**一覧のカードと吹き出しにも出る**。

    **語り手の顔とは別物。** 顔は辞書に 1 枚なので一覧には出さない（同じ絵が
    並ぶだけ）が、用語ごとの画像は語ごとに違うので見分けに効く。吹き出しでは
    同じ場所を取り合うので、**その語の画像があればそちらを出す**。
    """
    page.goto(f"{server}/glossary/登場人物/ジョバンニ")
    pick = page.locator("[data-ref=imagePick]")
    pick.wait_for(timeout=15000)
    # **入れる前でもボタンは出る**（隠すと最初の 1 枚を入れる道が無くなる）
    assert page.locator(".entry-photo").count() == 0

    page.set_input_files(
        "[data-ref=imageFile]",
        files=[{"name": "j.png", "mimeType": "image/png", "buffer": TINY_PNG}],
    )
    page.locator(".entry-photo").wait_for(timeout=15000)
    assert page.locator("[data-ref=imageDrop]").count() == 1

    # 一覧のカードにサムネイルが出る
    open_glossary(page, server, "?q=ジョバンニ")
    page.locator(".card .card-thumb").first.wait_for(timeout=15000)

    # 吹き出しでは顔ではなく**その語の画像**が出る
    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    link = page.locator("a.gloss-link", has_text="ジョバンニ").first
    link.wait_for(timeout=15000)
    link.hover()
    face = page.locator(".gloss-pop .pop-face.is-term")
    face.wait_for(timeout=10000)
    assert "/api/entry-image" in (face.get_attribute("src") or "")

    # 消せる（確認を通す）
    page.goto(f"{server}/glossary/登場人物/ジョバンニ")
    page.locator("[data-ref=imageDrop]").wait_for(timeout=15000)
    page.on("dialog", lambda d: d.accept())
    page.click("[data-ref=imageDrop]")
    page.locator("[data-ref=imagePick]").wait_for(timeout=15000)
    page.wait_for_function(
        "() => document.querySelectorAll('.entry-photo').length === 0", timeout=10000
    )


def test_the_graph_can_be_saved_as_an_image(page, server, seeded):
    """図を画像として保存できる（**6 つの見せ方すべてで同じ道**）。

    **画面の SVG をそのまま出すと崩れる** —— 見た目は CSS のクラスと変数で
    決まっていて、外に出た SVG からはどちらも引けない。計算済みの値を焼き込んで
    いることを、**保存した中身**で確かめる（拡張子だけ見ても分からない）。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        relations=[{"to": b.ref, "label": "級友", "back": "級友"}],
    ), ref=a.ref)

    page.goto(f"{server}/graph?category=登場人物")
    page.locator("svg.rel-graph").wait_for(timeout=15000)

    page.select_option("#saveKind", "svg")
    with page.expect_download(timeout=20000) as caught:
        page.click("#saveImage")
    download = caught.value
    assert download.suggested_filename.endswith(".svg")
    assert "登場人物" in download.suggested_filename

    body = pathlib.Path(download.path()).read_text(encoding="utf-8")
    # **寸法が要る**（外では入れ物が決めてくれない。地図の SVG を弾くのと同じ話）
    assert "<svg" in body and "width=" in body and "viewBox=" in body
    # **見た目が焼き込まれている。** クラス名だけ持って出ると素の黒い線になる
    assert "stroke:" in body and "font-family:" in body
    # 変数のまま出ていない（外では解決できない）
    assert "var(--" not in body
    # 一言も入っている（描いたものがそのまま入る）
    assert "級友" in body

    # PNG も出せる（貼る用）
    page.select_option("#saveKind", "png")
    with page.expect_download(timeout=20000) as caught:
        page.click("#saveImage")
    png = caught.value
    assert png.suggested_filename.endswith(".png")
    assert pathlib.Path(png.path()).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_saving_a_map_inlines_the_picture(page, server, seeded):
    """地図を保存すると、**絵まで 1 枚に畳まれる**。

    `/api/map?...` のままでは**こちらのサーバが動いていないと絵が出ない**
    （渡した相手には絶対に出ない）ので、保存する意味が半分になる。
    """
    _put_test_map()
    a = store.find_by_surface("ジョバンニ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        map="てすと図", pin=[0.24, 0.30],
    ), ref=a.ref)

    _open_map(page, server)
    page.select_option("#saveKind", "svg")
    with page.expect_download(timeout=20000) as caught:
        page.click("#saveImage")
    body = pathlib.Path(caught.value.path()).read_text(encoding="utf-8")

    assert "/api/map" not in body            # 外を指したままにしない
    assert "data:image/svg+xml;base64," in body or "data:image/" in body


def test_the_graph_can_show_only_what_has_been_read(page, server, seeded):
    """**ここまで読んだぶんだけ**（ビューアで読んでいるときだけ出る）。

    単位を揃えない —— 読書位置はブロックの添字、サーバの位置は文字位置なので、
    換算せず**本文のリンクそのもの**で「出てきた語」を決める（→ reading.js）。
    伏せた数は必ず注意書きに出す。
    """
    a = store.find_by_surface("ジョバンニ")[0]
    b = store.find_by_surface("カムパネルラ")[0]
    store.save(EntryDraft(
        term=a.term, category=a.category, definition=a.definition,
        relations=[{"to": b.ref, "label": "級友"}],
    ), ref=a.ref)
    # 2 人が別の段落に出てくる本文にする（先頭だけ読んだ状態を作れるように）
    doc = config.content_dir() / "銀河.md"
    doc.write_text(
        "# 午后の授業\n\n" + "ジョバンニは活版所で働いていた。\n\n"
        + "\n\n".join(f"それから何日も過ぎた。{i}" for i in range(40))
        + "\n\nカムパネルラは黙っていた。\n",
        encoding="utf-8",
    )

    page.goto(f"{server}/?open=%E9%8A%80%E6%B2%B3.md")
    page.locator("a.gloss-link").first.wait_for(timeout=15000)
    page.click("#docGraph")
    page.locator(".overlay svg.rel-graph").wait_for(timeout=15000)

    box = page.locator("#readSoFarBox")
    box.wait_for(timeout=10000)
    assert not box.is_hidden()          # ビューアの上に重ねているので出る

    page.check("#readSoFar")
    page.wait_for_function(
        "() => (document.querySelector('#notes')?.textContent || '')"
        ".includes('いま読んでいるところまで')",
        timeout=10000,
    )
    notes = page.text_content("#notes") or ""
    # 先頭しか読んでいないので、カムパネルラはまだ出ていない
    assert "1 語だけを出しています" in notes, notes
    assert "まだ出てきていない 1 語" in notes, notes

    # 外せば全体に戻る（サーバへ行き直さずに）
    page.uncheck("#readSoFar")
    page.wait_for_function(
        "() => !(document.querySelector('#notes')?.textContent || '')"
        ".includes('いま読んでいるところまで')",
        timeout=10000,
    )


def test_the_read_so_far_box_is_hidden_on_the_plain_graph_page(page, server, seeded):
    """`/graph` を直接開いたときは出さない（どこまで読んだかを知らない）。"""
    page.goto(f"{server}/graph")
    page.locator("svg.rel-graph, #canvas .empty").first.wait_for(timeout=15000)
    assert page.locator("#readSoFarBox").is_hidden()
