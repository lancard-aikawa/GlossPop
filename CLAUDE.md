# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

使い方・データ形式・自動リンクの規則は [README.md](README.md) にある。ここには
**README を読んでも分からないこと**（設計の前提、壊しやすい不変条件、開発時の落とし穴）
だけを書く。

## コマンド

```powershell
uv sync --all-groups            # 依存を入れる (dev グループ含む)
uv run glosspop serve           # http://127.0.0.1:8765/
uv run pytest                   # 全テスト
uv run pytest tests/test_linker.py::test_longest_match_wins   # 単体
uv run pytest -k "カテゴリ or category"
```

`glosspop` CLI は辞書操作もできる（`add` / `list` / `show` / `move` / `rm` / `categories`）。
`.claude/skills/gloss-add/SKILL.md` がその使い方をスキルとして持っている。**辞書の
スキーマや CLI の引数を変えたら SKILL.md も直すこと。**

### サーバの止め方（Windows で必ず踏む）

`uv run glosspop serve` の親プロセス（`uv`）を kill しても、**子の python が生き残って
ポートを掴んだまま**になる。新しいサーバは bind に失敗して黙って死に、古いコードが
動き続けるので「直したのに反映されない」という誤診をする。ポートの所有者を落とすこと。

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

`uv sync` が `glosspop.exe` を消せずに失敗するときも、たいていサーバが動いている。

## アーキテクチャ

データの流れは一本道:

```
ソース (ファイル / URL / 貼り付け)
  → render.render_source()      Markdown・テキスト・HTML → HTML
  → Linker.annotate()           テキストにだけ <a class="gloss-link"> を差し込む
  → ブラウザ                     popup.js が data-gloss から辞書を引いて吹き出しを出す
```

`store` が辞書、`categories` がカテゴリマスター、`ai` が Claude CLI 呼び出し。
`app.py` はこれらを繋ぐだけで、ロジックを持たない。

### 壊しやすい不変条件

**ディレクトリ名とファイル名が正。** エントリは `data/glossary/<カテゴリ>/<slug>.md`。
`category` と `slug` は frontmatter に書かない（書いても `_entry_from_file()` が
パスの値で上書きする）。一意キーは `Entry.ref` = `"<カテゴリ>/<slug>"` で、API も
URL もこの文字列を使う。**同じ用語名でもカテゴリが違えば別エントリ**なので、
`find_by_surface()` はリストを返す。単数を期待するコードを書かないこと。

**Linker はレンダリング済み HTML を文字列として走査する。** DOM は作らない。
`_tokenize()` でタグとテキストに切り、`_runs()` で「読者に見えるテキスト」の
まとまりを作ってから照合する。ここが 2 つの規則を担っている:

- `SKIP_TAGS`（`pre` `a` `script` `style` `textarea`）の内側はリンクしない
- `INLINE_TAGS` の中だけテキストを連結する。ブロック要素と `<br>` は連結しない
  → `**冪**等` は繋がるが `<p>冪</p><p>等</p>` は繋がらない

`render.md_to_html()` は `html: False` で生 HTML を通さない。これは安全のためだけで
なく、**Linker のタグ走査が想定外の構造を踏まないため**でもある。外部 HTML を
入れる経路（URL 読み込み）は `htmlclean` の許可制サニタイザを必ず通す。

**辞書本文は保存時に 1 文 1 行へ整形される**（`render.soften_paragraphs()`）。
表示側は `breaks: True` の markdown インスタンスを使う。ソース文書用の `_md` とは
別インスタンスなので、片方だけ設定を変えないこと。

### AI 下書き（`ai.py`）

`claude -p` をサブプロセスで叩く（API キー不要）。**プロジェクト内で実行すると
CLAUDE.md と `gloss-add` スキルを拾い、「重複を確認するため CLI を実行させて」と
要求して承認待ちで固まる。** そのため:

- cwd は `tempfile.gettempdir()/glosspop-ai`（プロジェクト外）
- `--disallowed-tools` で全ツールを落とし、`--disable-slash-commands` を付ける
- `--bare` は使えない（ANTHROPIC_API_KEY 必須になり、OAuth を読まなくなる）

プロンプトを触るときは、カテゴリの提案が既存へ寄りすぎないか実際に確かめること
（音楽用語を「プログラミング」に入れる、他カテゴリのサブカテゴリを流用する、
といった失敗を実際に踏んだ）。

## Windows ビルド（`packaging/`）

`.\packaging\build.ps1` で PyInstaller の onedir ビルド（`dist\GlossPop\`）を作る。
手順は README にある。ここには**壊しやすい点**だけ:

**凍結すると `__file__` は一時展開先（`_internal`）を指す。** そのため
`config.DATA_ROOT` は `sys.frozen` のとき `sys.executable` の親を基準にする。
ここを `PACKAGE_DIR` 基準に戻すと、**保存した辞書が起動ごとの一時ディレクトリに
書かれて消える**（サーバは正常に動いて見えるので気付きにくい）。逆に `STATIC_DIR` は
`_internal` 側で正しいので `PACKAGE_DIR` 基準のまま。

**文字列 import は凍結後に解決できない。** `cmd_serve()` は `--reload` のときだけ
`"glosspop.app:app"` を渡し、通常は app オブジェクトを直接渡す。ここを文字列に
戻すと exe が `Could not import module "glosspop.app"` で即死する。

**動的に読むものを足したら spec に足す。** 静的解析で追えないものは
`packaging/glosspop.spec` の `hiddenimports` / `datas` に書く必要がある
（`glosspop.*` は `collect_submodules` でまとめて入れてある）。新しい依存を
`pyproject.toml` に足したときは、**ビルドして exe を起動するところまで確認する**
——`uv run` では動いても凍結すると落ちる、という差が出るのはここだけ。

## フロントエンド

依存ゼロの ES モジュール。ビルド工程は無い。`static/` を編集したら
サーバ再起動は不要（ファイルはディスクから読まれる）。

`/static` と HTML には `Cache-Control: no-cache` を付けている。これが無いと
ブラウザが古い JS を出し、原因不明の壊れ方をする（実際に半日溶かした）。
`RevalidatingStatic` を消さないこと。

- `base.js` — API 呼び出し、DOM ヘルパ、URL 判定
- `select-add.js` — 選択 →「＋ 辞書に登録」。ビューアと辞書ページで共用
- `popup.js` / `editor.js` — 吹き出し・登録ダイアログ。どのページからも使える
- `viewer.js` / `glossary.js` / `entry.js` — 各ページ

**`prompt()` / `alert()` を入力 UI に使わない。** 一度使って作り直した。値の入力は
インラインの `<input>`/`<select>` にする（`confirm()` は削除確認にだけ使っている）。

## テスト

`tests/conftest.py` の autouse fixture が `config.GLOSSARY_DIR` などを tmp_path に
差し替える。**`store._ready` のリセットも fixture がやっている** ので、
`store.ensure_ready()` の初期化をテストで当てにするなら fixture を確認すること。

フロントエンドの自動テストは無い。UI を変えたら Playwright で実際に動かして確かめる
（このリポジトリの機能は「選択 → 登録 → リンクが生える」まで通して見ないと確認できない）。

## 変更時に一緒に直すもの

| 変えたもの | 一緒に直す |
| --- | --- |
| 辞書のスキーマ / CLI 引数 | `.claude/skills/gloss-add/SKILL.md` |
| 自動リンクの規則 | README.md の「自動リンクの規則」、`content/ようこそ.md` |
| カテゴリ名の制約 | `models.normalize_category()`、README、SKILL.md、`ai.build_prompt()` |
| 依存の追加 / 動的 import・データファイルの追加 | `packaging/glosspop.spec`（ビルドして exe 起動まで確認） |
| バージョン | `pyproject.toml` と `glosspop/__init__.py` の**両方**（タグと不一致だと release ワークフローが落ちる） |
