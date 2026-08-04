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
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright が入っていません")

from glosspop import config, store, updates  # noqa: E402
from glosspop.models import EntryDraft  # noqa: E402


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
