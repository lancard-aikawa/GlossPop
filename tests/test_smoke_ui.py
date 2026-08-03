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


@pytest.fixture
def page(server):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception as exc:                       # noqa: BLE001
            pytest.skip(f"Chrome を起動できません: {exc}")
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            yield page
            # JS の例外は画面に出ないことがある。黙って壊れたまま通さない
            assert not errors, f"ページで JS エラーが出ました: {errors}"
        finally:
            context.close()
            browser.close()


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


def test_the_doctor_is_quiet_then_reports_a_broken_reference(page, server, seeded):
    page.goto(f"{server}/doctor")
    page.locator("#root .empty").wait_for(timeout=15000)
    assert "直すところはありません" in page.locator("#root").inner_text()

    entry = store.find_by_surface("ジョバンニ")[0]
    store.save(
        EntryDraft(
            term=entry.term, category=entry.category,
            summary=entry.summary, definition=entry.definition,
            relations=[{"to": "いない人", "label": "兄"}],
        ),
        ref=entry.ref,
    )
    page.click("#reload")
    page.locator(".issue-badge.error").wait_for(timeout=10000)
    text = page.locator("#root").inner_text()
    assert "解決できない関係" in text
    # 壊れた参照は「次に書くべきエントリ」でもある
    assert "いない人 を登録" in text


def test_the_doctor_can_fix_an_entry_in_place(page, server, isolated_dirs):
    """点検からページを渡り歩かずに直せること（直したら消えるところまで）。"""
    store.save(EntryDraft(term="冪等", category="プログラミング", definition="本文。"))
    page.goto(f"{server}/doctor")
    page.locator(".issue-badge").wait_for(timeout=15000)
    assert "要約が無い" in page.locator("#root").inner_text()

    page.click("button:has-text('直す')")
    page.locator("dialog.sheet[open]").wait_for(timeout=10000)
    page.fill("dialog.sheet[open] [data-ref='summary']", "何度実行しても結果が同じであること。")
    page.click("dialog.sheet[open] [data-ref='save']")

    page.locator("#root .empty").wait_for(timeout=15000)
    assert "直すところはありません" in page.locator("#root").inner_text()


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


def test_the_update_notice_appears(page, server, isolated_dirs):
    """新しい版があると topbar に出る（押し付けず、リンク 1 本だけ）。"""
    page.goto(f"{server}/glossary")
    notice = page.locator(".topbar #update-notice")
    notice.wait_for(timeout=15000)
    assert "v99.0.0" in notice.inner_text()
    assert notice.get_attribute("href").endswith("/releases/latest")
